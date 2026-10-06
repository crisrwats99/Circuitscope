from PySide6.QtWidgets import QApplication, QPushButton

app = QApplication([])
button = QPushButton("Hello Circuitscope")
button.clicked.connect(lambda: print("clicked"))
button.show()
app.exec()