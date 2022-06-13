import contextlib
from multiprocessing import Array, Lock, Value

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from gridplayer.multiprocess.safe_shared_memory import SafeSharedMemory, releasing
from gridplayer.params.static import PLAYER_ID_LENGTH
from gridplayer.utils.qt import QT_ASPECT_MAP, qt_connect
from gridplayer.vlc_player.instance import InstanceProcessVLC
from gridplayer.vlc_player.libvlc import vlc
from gridplayer.vlc_player.player_base_threaded import VlcPlayerThreaded
from gridplayer.vlc_player.video_driver_base_threaded import VLCVideoDriverThreaded
from gridplayer.widgets.video_frame_vlc_base import VideoFrameVLCProcess


class ImageDecoder(object):
    def __init__(self, shared_memory, frame_ready_cb=None):
        super().__init__()

        self.is_paused = True

        self.lock_cb = self.libvlc_lock_callback()
        self.unlock_cb = self.libvlc_unlock_callback()

        self._stopped = False

        self._row_size = None
        self._width = None
        self._height = None

        self._shared_memory = shared_memory
        self._frame_ready_cb = frame_ready_cb

        self._prev_frame_head = None

    def set_frame(self, width, height):
        self._width = width
        self._height = height

        self._row_size = self._width * 4

        buf_size = self._height * self._row_size

        self._shared_memory.allocate(buf_size)

    def attach_media_player(self, media_player):
        media_player.video_set_callbacks(self.lock_cb, self.unlock_cb, None, None)
        media_player.video_set_format("RV32", self._width, self._height, self._row_size)

    def libvlc_lock_callback(self):
        @vlc.CallbackDecorators.VideoLockCb
        def _cb(opaque, planes):  # noqa: WPS430
            self._shared_memory.lock.acquire()
            planes[0] = self._shared_memory.ptr

        return _cb

    def libvlc_unlock_callback(self):
        @vlc.CallbackDecorators.VideoUnlockCb
        def _cb(opaque, picta, planes):  # noqa: WPS430
            with releasing(self._shared_memory.lock):
                if self._stopped:
                    return

                # Callback is firing while on pause,
                # so check if the frame content actually changed
                if self.is_paused and not self._is_frame_changed():
                    return

                self._frame_ready_cb()

        return _cb

    def stop(self):
        self._stopped = True

        # make sure that memory lock released in case it was locked mid-callback
        with contextlib.suppress(ValueError):
            self._shared_memory.lock.release()

        self._shared_memory.close()

    def _is_frame_changed(self):
        new_frame_head = bytes(self._shared_memory.memory.buf[:1024])

        if new_frame_head == self._prev_frame_head:
            return False

        self._prev_frame_head = new_frame_head
        return True


class InstanceProcessVLCSW(InstanceProcessVLC):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # shared data multiprocess
        self._memory_locks = [
            {
                "lock": Lock(),
                "is_busy": Value("i", 0),
                "player_id": Array("c", PLAYER_ID_LENGTH * 2),
            }
            for _ in range(self.players_per_instance)
        ]

        self._vlc.vlc_options = ["--vout=vdummy"]

    def init_player_shared_data(self, player_id):
        available_locks = (ml for ml in self._memory_locks if ml["is_busy"].value == 0)

        player_lock = next(available_locks)

        player_lock["is_busy"].value = 1
        player_lock["player_id"].value = player_id.encode()

        self._players_shared_data[player_id] = self.get_player_shared_memory(player_id)

    def release_player_shared_data(self, player_id):
        player_lock = self.get_player_lock(player_id)

        player_lock["player_id"].value = b""
        player_lock["is_busy"].value = 0

    def get_player_lock(self, player_id):
        return next(
            ml
            for ml in self._memory_locks
            if ml["player_id"].value == player_id.encode()
        )

    def get_player_shared_memory(self, player_id):
        player_lock = self.get_player_lock(player_id)
        return SafeSharedMemory(player_id, player_lock["lock"])

    def new_player(self, player_id, init_data, pipe):
        init_data["shared_memory"] = self.get_player_shared_memory(player_id)

        player = PlayerProcessSingleVLCSW(
            player_id=player_id,
            release_callback=self.release_player,
            init_data=init_data,
            vlc_instance=self.vlc_instance,
            crash_func=self.crash,
            pipe=pipe,
        )
        self._players[player_id] = player


class PlayerProcessSingleVLCSW(VlcPlayerThreaded):
    def __init__(self, player_id, release_callback, init_data, **kwargs):
        super().__init__(**kwargs)

        self.id = player_id
        self.release_callback = release_callback

        self.shared_memory = init_data["shared_memory"]
        self.decoder = None

        self._is_decoder_initialized = False

        # Disable hardware decoding
        self._media_options.append("avcodec-hw=none")

        self.start()

    def init_player(self):
        super().init_player()

        self.decoder = ImageDecoder(self.shared_memory, self.ready_signal)

    def ready_signal(self):
        self.cmd_send("process_image")

    def cleanup(self):
        super().cleanup()

        self.decoder.stop()

        self.release_callback(self.id)

    def cleanup_final(self):
        self.cmd_loop_terminate()

    def load_video_st2_set_parsed_media(self):
        if self.media_track:
            self._init_video_decoder()

        super().load_video_st2_set_parsed_media()

    def load_video_st4_loaded(self):
        if not self._is_decoder_initialized:
            # Since we need metadata to allocate video buffer, restart is required
            self._restart_playback()
            return

        super().load_video_st4_loaded()

    def play(self):
        self.decoder.is_paused = False

        super().play()

    def set_pause(self, is_paused):
        self.decoder.is_paused = is_paused

        super().set_pause(is_paused)

    def _init_video_decoder(self):
        self._is_decoder_initialized = True

        width, height = self.media_track.video_dimensions

        self.decoder.set_frame(width, height)
        self.decoder.attach_media_player(self._media_player)

        self.cmd_send("init_frame", width, height)

    def _restart_playback(self):
        self._log.debug("Restarting playback...")

        self.stop()

        self.cmd_send("load_video", self.media_input)


class VideoDriverVLCSW(VLCVideoDriverThreaded):
    set_dummy_frame_sig = pyqtSignal()
    image_ready_sig = pyqtSignal()

    def __init__(self, image_dest, process_manager, **kwargs):
        super().__init__(**kwargs)

        self._width = None
        self._height = None

        self._image_dest = image_dest
        self._shared_memory = None

        self._pix = None

        qt_connect(
            (self.set_dummy_frame_sig, self.set_dummy_frame),
            (self.image_ready_sig, self.image_ready),
        )

        self.player = process_manager.init_player({}, self.cmd_child_pipe())

    def init_frame(self, width, height):
        self._shared_memory = self.player.get_player_shared_data()

        self._width = width
        self._height = height

        self.set_dummy_frame_sig.emit()

    def set_dummy_frame(self):
        pix = QPixmap(self._width, self._height)
        pix.fill(Qt.black)
        self._image_dest.setPixmap(pix)

    def process_image(self):
        if self._shared_memory is None:
            return

        try:
            with self._shared_memory:
                px = QImage(
                    self._shared_memory.memory.buf,
                    self._width,
                    self._height,
                    QImage.Format_RGB32,
                )
        except AttributeError:
            # Very rare race condition
            self._log.warning("Shared memory is cleared already")
            return

        self._pix = QPixmap.fromImage(px)

        self.image_ready_sig.emit()

    def image_ready(self):
        self._image_dest.setPixmap(self._pix)

    def cleanup(self):
        if self._shared_memory is not None:
            with self._shared_memory:
                self._shared_memory.close()
                self._shared_memory = None

        super().cleanup()

        self.player.cleanup()


class VideoFrameVLCSW(VideoFrameVLCProcess):
    is_opengl = False

    def driver_setup(self) -> VideoDriverVLCSW:
        return VideoDriverVLCSW(
            image_dest=self._videoitem,
            process_manager=self.process_manager,
            parent=self,
        )

    def ui_video_surface(self):  # noqa: WPS213
        self._videoitem = QGraphicsPixmapItem()
        self._videoitem.setTransformationMode(Qt.SmoothTransformation)
        self._videoitem.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)

        self._scene = QGraphicsScene(self)
        self._scene.addItem(self._videoitem)

        video_surface = QGraphicsView(self._scene, self)
        video_surface.setBackgroundBrush(QBrush(Qt.black))
        video_surface.setWindowFlags(Qt.WindowTransparentForInput)
        video_surface.setAttribute(Qt.WA_TransparentForMouseEvents)
        video_surface.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        video_surface.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        video_surface.setFrameStyle(QFrame.NoFrame)
        video_surface.setLineWidth(0)
        video_surface.setRenderHints(
            QPainter.Antialiasing
            | QPainter.SmoothPixmapTransform
            | QPainter.TextAntialiasing
            | QPainter.HighQualityAntialiasing
        )

        return video_surface

    def cleanup(self):
        if super().cleanup():
            return

        # need to remove it before cleanup to avoid occasional segmentation fault
        self._scene.removeItem(self._videoitem)

    def take_snapshot(self) -> None:
        # no need to take snapshot, last frame stays in QGraphicsView on stop
        self.video_driver.set_pause(True)

    def adjust_view(self):
        if super().adjust_view():
            return

        aspect = QT_ASPECT_MAP[self._aspect]

        self.video_surface.fitInView(self._videoitem, aspect)
        black_border_cut = 0.05
        self.video_surface.scale(
            self._scale + black_border_cut, self._scale + black_border_cut
        )
