import contextlib
import logging
import subprocess

from PyQt5.QtCore import QLocale, QUrl
from PyQt5.QtGui import QDesktopServices, QIcon
from PyQt5.QtWidgets import QCheckBox, QComboBox, QDialog, QSpinBox

from gridplayer.dialogs.messagebox import QCustomMessageBox
from gridplayer.dialogs.settings_dialog_ui import Ui_SettingsDialog
from gridplayer.params import env
from gridplayer.params.static import (
    SUPPORTED_LANGUAGES,
    GridMode,
    SeekSyncMode,
    VideoAspect,
    VideoDriver,
    VideoRepeat,
)
from gridplayer.settings import Settings
from gridplayer.utils import log_config
from gridplayer.utils.app_dir import get_app_data_dir
from gridplayer.utils.qt import qt_connect, translate

VIDEO_DRIVERS_MULTIPROCESS = (
    VideoDriver.VLC_SW,
    VideoDriver.VLC_HW,
)

MAX_VLC_PROCESSES = 64


def _fill_combo_box(combo_box, values_dict):
    for i_id, i_name in values_dict.items():
        combo_box.addItem(i_name, i_id)


def _set_combo_box(combo_box, data_value):
    idx = combo_box.findData(data_value)
    combo_box.setCurrentIndex(idx)


def _set_groupbox_header_bold(groupbox):
    font = groupbox.font()
    font.setBold(True)
    groupbox.setFont(font)

    # Restore the font of each children to regular.
    font.setBold(False)
    for child in groupbox.children():
        with contextlib.suppress(AttributeError):
            child.setFont(font)


class SettingsDialog(QDialog, Ui_SettingsDialog):
    def __init__(self, parent):
        super().__init__(parent)

        self._log = logging.getLogger(self.__class__.__name__)

        self.setupUi(self)

        self.settings_map = {
            "player/video_driver": self.playerVideoDriver,
            "player/video_driver_players": self.playerVideoDriverPlayers,
            "player/pause_background_videos": self.playerPauseBackgroundVideos,
            "player/pause_minimized": self.playerPauseWhenMinimized,
            "player/inhibit_screensaver": self.playerInhibitScreensaver,
            "player/one_instance": self.playerOneInstance,
            "player/language": self.language,
            "playlist/grid_mode": self.gridMode,
            "playlist/grid_fit": self.gridFit,
            "playlist/grid_size": self.gridSize,
            "playlist/save_position": self.playlistSavePosition,
            "playlist/save_state": self.playlistSaveState,
            "playlist/save_window": self.playlistSaveWindow,
            "playlist/seek_sync_mode": self.playlistSeekSyncMode,
            "playlist/track_changes": self.playlistTrackChanges,
            "video_defaults/aspect": self.videoAspect,
            "video_defaults/repeat": self.repeatMode,
            "video_defaults/random_loop": self.videoRandomLoop,
            "video_defaults/muted": self.videoMuted,
            "video_defaults/paused": self.videoPaused,
            "video_defaults/stream_quality": self.streamQuality,
            "misc/overlay_hide": self.timeoutOverlayFlag,
            "misc/overlay_timeout": self.timeoutOverlay,
            "misc/mouse_hide": self.timeoutMouseHideFlag,
            "misc/mouse_hide_timeout": self.timeoutMouseHide,
            "logging/log_level": self.logLevel,
            "logging/log_level_vlc": self.logLevelVLC,
            "internal/opaque_hw_overlay": self.miscOpaqueHWOverlay,
        }

        self.ui_customize()
        self.ui_fill()

        self.ui_connect()

        self.load_settings()

        self.ui_customize_dynamic()

    def ui_customize(self):  # noqa: WPS213
        for btn in self.buttonBox.buttons():
            btn.setIcon(QIcon())

        _set_groupbox_header_bold(self.playerVideoDriverBox)
        _set_groupbox_header_bold(self.languageBox)

        if env.IS_MACOS:
            self.lay_body.setContentsMargins(4, 0, 0, 0)
            self.horizontalLayout.setContentsMargins(0, 0, 15, 0)  # noqa: WPS432
            self.languageBox.setContentsMargins(0, 0, 15, 0)  # noqa: WPS432
            self.lay_playerVideoDriverPlayers.setContentsMargins(3, 0, 2, 0)
            self.playerVideoDriverBox.setStyleSheet(
                "QGroupBox:title{padding: 0 4px 0 3px;margin-left:-5px;}"
            )
            self.languageBox.setStyleSheet(
                "QGroupBox:title{padding: 0 4px 0 3px;margin-left:-5px;}"
            )

            self.lay_boxes.setSpacing(25)  # noqa: WPS432

            combo_boxes = (
                e for e in self.settings_map.values() if isinstance(e, QComboBox)
            )
            for cb in combo_boxes:
                cb.setMaximumHeight(22)  # noqa: WPS432

        if not env.IS_LINUX:
            self.section_misc.hide()
            self.miscOpaqueHWOverlay.hide()

    def ui_fill(self):
        self.fill_playerVideoDriver()
        self.fill_gridMode()
        self.fill_videoAspect()
        self.fill_repeatMode()
        self.fill_logLevel()
        self.fill_logLevelVLC()
        self.fill_language()
        self.fill_streamQuality()
        self.fill_playlistSeekSyncMode()

    def ui_customize_dynamic(self):
        self.driver_selected(self.playerVideoDriver.currentIndex())
        self.timeoutMouseHide.setEnabled(self.timeoutMouseHideFlag.isChecked())
        self.timeoutOverlay.setEnabled(self.timeoutOverlayFlag.isChecked())

        self.playerVideoDriverPlayers.setRange(1, MAX_VLC_PROCESSES)
        self.timeoutOverlay.setRange(1, 60)
        self.timeoutMouseHide.setRange(1, 60)

        self.gridSize.setRange(0, 1000)
        self.gridSize.setSpecialValueText(self.tr("Auto"))

    def ui_connect(self):
        qt_connect(
            (self.playerVideoDriver.currentIndexChanged, self.driver_selected),
            (self.timeoutMouseHideFlag.stateChanged, self.timeoutMouseHide.setEnabled),
            (self.timeoutOverlayFlag.stateChanged, self.timeoutOverlay.setEnabled),
            (self.logFileOpen.clicked, self.open_logfile),
        )

    def open_logfile(self):
        log_path = get_app_data_dir() / "gridplayer.log"

        self._log.debug(f"Opening log file {log_path}")

        if not log_path.is_file():
            return QCustomMessageBox.critical(
                self, self.tr("Error"), self.tr("Log file does not exist!")
            )

        if env.IS_SNAP:
            # https://forum.snapcraft.io/t/xdg-open-or-gvfs-open-qdesktopservices-openurl-file-somelocation-file-txt-wont-open-the-file/16824
            subprocess.call(["xdg-open", log_path])  # noqa: S603, S607
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))

    def fill_logLevelVLC(self):
        log_levels = {
            log_config.DISABLED: translate("ErrorLevel", "None"),
            logging.ERROR: translate("ErrorLevel", "Error"),
            logging.WARNING: translate("ErrorLevel", "Warning"),
            logging.INFO: translate("ErrorLevel", "Info"),
            logging.DEBUG: translate("ErrorLevel", "Debug"),
        }

        _fill_combo_box(self.logLevelVLC, log_levels)

    def fill_logLevel(self):
        log_levels = {
            log_config.DISABLED: translate("ErrorLevel", "None"),
            logging.CRITICAL: translate("ErrorLevel", "Critical"),
            logging.ERROR: translate("ErrorLevel", "Error"),
            logging.WARNING: translate("ErrorLevel", "Warning"),
            logging.INFO: translate("ErrorLevel", "Info"),
            logging.DEBUG: translate("ErrorLevel", "Debug"),
        }

        _fill_combo_box(self.logLevel, log_levels)

    def fill_videoAspect(self):
        aspect_ratios = {
            VideoAspect.FIT: self.tr("Fit"),
            VideoAspect.STRETCH: self.tr("Stretch"),
            VideoAspect.NONE: self.tr("None"),
        }

        _fill_combo_box(self.videoAspect, aspect_ratios)

    def fill_repeatMode(self):
        repeat_modes = {
            VideoRepeat.SINGLE_FILE: self.tr("Single File"),
            VideoRepeat.DIR: self.tr("Directory"),
            VideoRepeat.DIR_SHUFFLE: self.tr("Directory (Shuffle)"),
        }

        _fill_combo_box(self.repeatMode, repeat_modes)

    def fill_gridMode(self):
        grid_modes = {
            GridMode.AUTO_ROWS: self.tr("Rows First"),
            GridMode.AUTO_COLS: self.tr("Columns First"),
        }

        _fill_combo_box(self.gridMode, grid_modes)

    def fill_playerVideoDriver(self):
        if env.IS_MACOS:
            video_drivers = {
                VideoDriver.VLC_HW_SP: "{0} <VLC {1}>".format(
                    self.tr("Hardware SP"), env.VLC_VERSION
                ),
                VideoDriver.VLC_SW: "{0} <VLC {1}>".format(
                    self.tr("Software"), env.VLC_VERSION
                ),
                VideoDriver.DUMMY: self.tr("Dummy"),
            }
        else:
            video_drivers = {
                VideoDriver.VLC_HW: "{0} <VLC {1}>".format(
                    self.tr("Hardware"), env.VLC_VERSION
                ),
                VideoDriver.VLC_HW_SP: "{0} <VLC {1}>".format(
                    self.tr("Hardware SP"), env.VLC_VERSION
                ),
                VideoDriver.VLC_SW: "{0} <VLC {1}>".format(
                    self.tr("Software"), env.VLC_VERSION
                ),
                VideoDriver.DUMMY: self.tr("Dummy"),
            }

        _fill_combo_box(self.playerVideoDriver, video_drivers)

    def fill_language(self):
        languages = {
            lang: "{0} ({1})".format(
                QLocale.languageToString(QLocale(lang).language()),
                QLocale.countryToString(QLocale(lang).country()),
            )
            if lang in {"zh_CN", "zh_TW"}
            else QLocale.languageToString(QLocale(lang).language())
            for lang in SUPPORTED_LANGUAGES
        }
        sorted_languages = dict(sorted(languages.items(), key=lambda x: x[1]))

        _fill_combo_box(self.language, sorted_languages)

    def fill_streamQuality(self):
        quality_codes = {
            "best": self.tr("Best"),
            "worst": self.tr("Worst"),
        }

        standard_quality_codes = [
            "2160p",
            "2160p60",
            "1440p",
            "1440p60",
            "1080p",
            "1080p60",
            "720p60",
            "720p",
            "480p",
            "360p",
            "240p",
            "144p",
        ]

        for code in standard_quality_codes:
            quality_codes[code] = code

        _fill_combo_box(self.streamQuality, quality_codes)

    def fill_playlistSeekSyncMode(self):
        seek_modes = {
            SeekSyncMode.NONE: self.tr("None"),
            SeekSyncMode.PERCENT: self.tr("Percent"),
            SeekSyncMode.TIMECODE: self.tr("Timecode"),
        }

        _fill_combo_box(self.playlistSeekSyncMode, seek_modes)

    def driver_selected(self, idx):
        driver_id = self.playerVideoDriver.itemData(idx)

        if driver_id in VIDEO_DRIVERS_MULTIPROCESS:
            self.playerVideoDriverPlayers.setDisabled(False)
        else:
            self.playerVideoDriverPlayers.setDisabled(True)

    def load_settings(self):
        elements_value_set_fun = {
            QCheckBox: lambda e, v: e.setChecked(v),
            QSpinBox: lambda e, v: e.setValue(v),
            QComboBox: _set_combo_box,
        }

        for setting, element in self.settings_map.items():
            setting_value = Settings().get(setting)

            try:
                set_function = elements_value_set_fun[type(element)]
            except KeyError:
                raise ValueError(f"No element decoder for {setting}")

            set_function(element, setting_value)

    def save_settings(self):
        elements_value_read_attr = {
            QCheckBox: "isChecked",
            QSpinBox: "value",
            QComboBox: "currentData",
        }

        for setting, element in self.settings_map.items():
            if not element.isEnabled():
                continue

            try:
                value_attr = elements_value_read_attr[type(element)]
            except KeyError:
                raise ValueError(f"No element decoder for {setting}")

            new_value = getattr(element, value_attr)()

            Settings().set(setting, new_value)

    def accept(self):
        self.save_settings()

        super().accept()
