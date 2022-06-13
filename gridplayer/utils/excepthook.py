import logging
import traceback

from PyQt5.QtWidgets import QApplication

from gridplayer.dialogs.exception import ExceptionDialog
from gridplayer.utils.misc import force_terminate


def excepthook(exc_type, exc_value, exc_tb):
    # Log into file
    exception_txt = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

    logger = logging.getLogger("UNHANDLED")
    logger.critical(exception_txt)

    # Terminate if Qt is not up
    if QApplication.instance() is None:
        force_terminate(1)

    for w in QApplication.topLevelWidgets():
        w.hide()
    QApplication.restoreOverrideCursor()

    exc_dialog = ExceptionDialog(exception_txt)
    exc_dialog.exec_()

    force_terminate()
