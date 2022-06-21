from pathlib import Path

from PyQt5.QtCore import QDir, pyqtSignal
from PyQt5.QtWidgets import QFileDialog, QMessageBox

from gridplayer.dialogs.messagebox import QCustomMessageBox
from gridplayer.models.grid_state import GridState
from gridplayer.models.playlist import Playlist
from gridplayer.models.video import filter_video_uris
from gridplayer.params.static import SeekSyncMode, WindowState
from gridplayer.player.managers.base import ManagerBase
from gridplayer.settings import Settings
from gridplayer.utils.files import get_playlist_path
from gridplayer.utils.qt import tr


class PlaylistManager(ManagerBase):
    playlist_closed = pyqtSignal()
    playlist_loaded = pyqtSignal()
    window_state_loaded = pyqtSignal(WindowState)
    grid_state_loaded = pyqtSignal(GridState)
    seek_sync_mode_loaded = pyqtSignal(SeekSyncMode)
    videos_loaded = pyqtSignal(list)

    alert = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self._saved_playlist = None

    @property
    def commands(self):
        return {
            "open_playlist": self.cmd_open_playlist,
            "save_playlist": self.cmd_save_playlist,
            "close_playlist": self.cmd_close_playlist,
        }

    def cmd_open_playlist(self):
        dialog = QFileDialog(self.parent())
        dialog.setFileMode(QFileDialog.ExistingFile)

        dialog.setNameFilter("{0} (*.gpls)".format(tr("GridPlayer Playlists")))

        if dialog.exec():
            files = dialog.selectedFiles()

            if files:
                self.load_playlist_file(files[0])

    def cmd_close_playlist(self):
        self.check_playlist_save()

        self.playlist_closed.emit()

    def cmd_save_playlist(self):
        playlist = self._make_playlist()

        if self._saved_playlist is not None:
            save_path = self._saved_playlist["path"]
        else:
            save_path = Path(QDir.homePath()) / "Untitled.gpls"

        self._log.debug(f"Proposed playlist save path: {save_path}")

        file_path, _ = QFileDialog.getSaveFileName(
            self.parent(), tr("Where to save playlist"), str(save_path), "*.gpls"
        )

        if not file_path:
            return

        file_path = Path(file_path)

        # filename placeholder is not available if file doesn't exist
        # problematic for new playlists, need to prevent accidental overwrite
        # occurs in Flatpak, maybe in other sandboxes that use portal
        if file_path.suffix.lower() != ".gpls":
            file_path = file_path.with_suffix(".gpls")

            if self._is_overwrite_denied(file_path):
                return

        playlist.save(file_path)

        self._saved_playlist = {
            "path": file_path,
            "state": hash(playlist.dumps()),
        }

    def process_arguments(self, argv):
        if not argv:
            return

        playlist = get_playlist_path(argv)

        if playlist:
            self.load_playlist_file(playlist)
            return

        videos = filter_video_uris(argv)

        if not videos:
            self.error.emit(tr("No supported files or URLs!"))
            return

        self.videos_loaded.emit(videos)

        self.alert.emit()

    def load_playlist_file(self, playlist_file: Path):
        try:
            playlist = Playlist.read(playlist_file)
        except ValueError as e:
            self._log.error(f"Playlist parse error: {e}")
            self.error.emit(
                "{0}\n\n{1}".format(tr("Invalid playlist format!"), playlist_file)
            )
            return
        except FileNotFoundError:
            self.error.emit("{0}\n\n{1}".format(tr("File not found!"), playlist_file))
            return

        if not playlist.videos:
            self.error.emit(
                "{0}\n\n{1}".format(tr("Empty or invalid playlist!"), playlist_file)
            )
            return

        self.load_playlist(playlist)

        self._saved_playlist = {
            "path": playlist_file,
            "state": hash(self._make_playlist().dumps()),
        }

    def load_playlist(self, playlist: Playlist):
        self.cmd_close_playlist()

        self.videos_loaded.emit(playlist.videos)

        if playlist.grid_state is not None:
            self.grid_state_loaded.emit(playlist.grid_state)

        if playlist.window_state is not None:
            self.window_state_loaded.emit(playlist.window_state)

        self.seek_sync_mode_loaded.emit(playlist.seek_sync_mode)

        self.alert.emit()

    def check_playlist_save(self):
        if not Settings().get("playlist/track_changes"):
            return

        if not self._ctx.video_blocks:
            return

        if self._saved_playlist is not None:
            playlist_state = hash(self._make_playlist().dumps())

            is_playlist_changed = playlist_state != self._saved_playlist["state"]
        else:
            is_playlist_changed = True

        if is_playlist_changed:
            self.alert.emit()

            ret = QCustomMessageBox.question(
                self.parent(), tr("Playlist"), tr("Do you want to save the playlist?")
            )

            if ret == QMessageBox.Yes:
                self.cmd_save_playlist()

    def _is_overwrite_denied(self, file_path: Path):
        if file_path.is_file():
            q_message = tr("Do you want to overwrite {FILE_NAME}?").replace(
                "{FILE_NAME}", file_path.name
            )

            ret = QCustomMessageBox.question(self.parent(), tr("Playlist"), q_message)

            if ret != QMessageBox.No:
                return True

        return False

    def _make_playlist(self):
        return Playlist(
            grid_state=self._ctx.grid_state,
            window_state=self._ctx.window_state,
            videos=self._ctx.video_blocks.videos,
            seek_sync_mode=self._ctx.seek_sync_mode,
        )
