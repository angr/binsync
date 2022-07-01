from binsync.common.ui.version import ui_version

if ui_version == "PySide2":
    from PySide2.QtCore import QDir, Qt, Signal
    from PySide2.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMenu,
        QMessageBox,
        QPushButton,
        QStatusBar,
        QStyledItemDelegate,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
        QDialogButtonBox,
        QAction,
    )
    from PySide2.QtGui import QFontDatabase
elif ui_version == "PySide6":
    from PySide6.QtCore import QDir, Qt, Signal
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMenu,
        QMessageBox,
        QPushButton,
        QStatusBar,
        QStyledItemDelegate,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
        QDialogButtonBox,
        QAction,
    )
    from PySide6.QtGui import QFontDatabase
else:
    from PyQt5.QtCore import QDir, Qt
    from PyQt5.QtCore import pyqtSignal as Signal
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMenu,
        QMessageBox,
        QPushButton,
        QStatusBar,
        QStyledItemDelegate,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
        QDialogButtonBox,
        QAction
    )
    from PyQt5.QtGui import QFontDatabase
