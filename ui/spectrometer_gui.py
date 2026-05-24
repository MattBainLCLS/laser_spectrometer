"""Standalone spectrometer window."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QMainWindow, QStatusBar

from ui.spectrometer_widget import SpectrometerWidget


class SpectrometerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spectrometer")
        self.setMinimumSize(960, 640)

        self._widget = SpectrometerWidget()
        self._widget.status_message.connect(self.statusBar().showMessage)
        self.setCentralWidget(self._widget)

    def closeEvent(self, event):
        self._widget.shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = SpectrometerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
