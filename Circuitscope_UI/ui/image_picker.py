"""
ui/image_picker.py

Two small widgets for adding PICTURES to a question or item (a circuit diagram, a
scope trace, a figure the theory question refers to):

    PromptEdit   - a multi-line text box for the question text. If a picture is
                   pasted into it (Ctrl+V after copying an image or taking a
                   screenshot with Win+Shift+S) it hands the picture over instead
                   of ignoring it.
    ImagePicker  - shows the picture and has "Choose image...", "Paste image" and
                   "Remove image" buttons.

Pictures are stored INSIDE the assessment file as text (PNG, then base64), so a
practical stays one single file. They are shrunk first so the file stays small.
"""

from __future__ import annotations

import base64

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from core.models import MAX_IMAGE_BYTES

MAX_SIDE_PX = 1200                 # pictures are shrunk so neither side is longer than this
THUMBNAIL_SIZE = (320, 200)        # size of the preview on the editor screen
RED = "color: #b00020;"


def qimage_to_b64(image: QImage) -> str:
    """
    Turn a picture into PNG text (base64) small enough to store in the assessment.
    If the PNG is still too big it is shrunk further until it fits.
    """
    side = MAX_SIDE_PX
    while True:
        picture = image
        if max(picture.width(), picture.height()) > side:
            picture = picture.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        picture.save(buffer, "PNG")
        data = bytes(buffer.data())
        # base64 text is about 4/3 the size of the raw bytes, so compare with 3/4 of the limit.
        if len(data) <= MAX_IMAGE_BYTES * 3 // 4 or side <= 200:
            return base64.b64encode(data).decode("ascii")
        side = int(side * 0.75)


def b64_to_pixmap(image_b64: str) -> QPixmap:
    """The picture stored as text, ready to display (null if the text is damaged)."""
    pixmap = QPixmap()
    try:
        pixmap.loadFromData(QByteArray(base64.b64decode(image_b64)), "PNG")
    except (ValueError, TypeError):
        pass
    return pixmap


class PromptEdit(QPlainTextEdit):
    """A text box that also accepts a pasted picture (it is sent on as `image_pasted`)."""

    image_pasted = Signal(QImage)

    def canInsertFromMimeData(self, source) -> bool:
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        if source.hasImage():
            data = source.imageData()
            image = data if isinstance(data, QImage) else data.toImage()
            if not image.isNull():
                self.image_pasted.emit(image)
            return
        super().insertFromMimeData(source)


class ImagePicker(QWidget):
    """Shows one optional picture, with buttons to choose, paste or remove it."""

    changed = Signal()                 # the picture was added, replaced or removed

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._b64: str | None = None
        self.preview = QLabel("No picture")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(40)
        self.preview.setStyleSheet("color: #777;")
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.choose_button = QPushButton("Choose image...")
        self.paste_button = QPushButton("Paste image")
        self.remove_button = QPushButton("Remove image")

        row = QHBoxLayout()
        for button in (self.choose_button, self.paste_button, self.remove_button):
            row.addWidget(button)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.preview)
        layout.addLayout(row)
        layout.addWidget(self.message)

        self.choose_button.clicked.connect(self.choose_file)
        self.paste_button.clicked.connect(self.paste_from_clipboard)
        self.remove_button.clicked.connect(self.clear)
        self._show()

    # ---- the stored picture ----------------------------------------------------

    @property
    def image_b64(self) -> str | None:
        return self._b64

    def set_image_b64(self, image_b64: str | None) -> None:
        """Show a picture that is already stored (used when loading; sends no signal)."""
        self._b64 = image_b64 or None
        self._show()

    def set_qimage(self, image: QImage) -> bool:
        """Use a picture from memory (pasted or read from a file). Sends `changed`."""
        if image.isNull():
            self._say("That picture is empty, so it was not added.", error=True)
            return False
        self._b64 = qimage_to_b64(image)
        self._show()
        self.changed.emit()
        return True

    def clear(self) -> None:
        if self._b64 is not None:
            self._b64 = None
            self._show()
            self.changed.emit()

    # ---- ways of getting a picture ------------------------------------------------

    def choose_file(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a picture", "",
                                              "Pictures (*.png *.jpg *.jpeg *.bmp *.gif)")
        return self.load_file(path) if path else False

    def load_file(self, path: str) -> bool:
        image = QImage(path)
        if image.isNull():
            self._say(f"Could not read '{path}' as a picture. Use a PNG, JPG, BMP or GIF file.", error=True)
            return False
        return self.set_qimage(image)

    def paste_from_clipboard(self) -> bool:
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self._say("The clipboard has no picture. Copy one first (for a screenshot press "
                      "Win+Shift+S), then click 'Paste image'.", error=True)
            return False
        return self.set_qimage(image)

    # ---- display -----------------------------------------------------------------------

    def _say(self, text: str, error: bool = False) -> None:
        self.message.setText(text)
        self.message.setStyleSheet(RED if error else "")

    def _show(self) -> None:
        pixmap = b64_to_pixmap(self._b64) if self._b64 else QPixmap()
        if self._b64 and not pixmap.isNull():
            self.preview.setPixmap(pixmap.scaled(*THUMBNAIL_SIZE, Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))
            self._say(f"Picture stored: {pixmap.width()} x {pixmap.height()} pixels.")
        elif self._b64:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Picture cannot be shown")
            self._say("The stored picture is damaged. Remove it and add it again.", error=True)
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("No picture")
            self._say("")
        self.remove_button.setEnabled(self._b64 is not None)
