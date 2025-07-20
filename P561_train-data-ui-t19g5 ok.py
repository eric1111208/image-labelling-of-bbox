#display annotation in textbox pixels value at textbox, normalize value at .txt
#Added Resolution Label
#shows the actual pixel position within the image, not the widget position
import json
import os
import sys

from PIL import Image, ExifTags
from PyQt5.QtCore import QPoint, Qt, QRectF, QPointF
from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor, QPen
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QTabWidget,
                             QVBoxLayout, QHBoxLayout, QListWidget, QPushButton,
                             QLabel, QLineEdit, QTextEdit, QFileDialog, QInputDialog,
                             QSplitter, QMessageBox)


def load_image_correct_orientation(image_path):
    try:
        pil_img = Image.open(image_path)
        try:
            exif = pil_img._getexif()
            if exif:
                orientation_key = next(k for k, v in ExifTags.TAGS.items() if v == 'Orientation')
                orientation = exif.get(orientation_key, 1)
                if orientation == 3:
                    pil_img = pil_img.rotate(180, expand=True)
                elif orientation == 6:
                    pil_img = pil_img.rotate(270, expand=True)
                elif orientation == 8:
                    pil_img = pil_img.rotate(90, expand=True)
        except Exception as e:
            print("EXIF read failed:", e)
        pil_img = pil_img.convert("RGB")
        data = pil_img.tobytes("raw", "RGB")
        qimage = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGB888)
        return qimage
    except Exception as e:
        print(f"Failed to load image {image_path}: {e}")
        return QImage()


class ZoomableLabel(QLabel):
    def __init__(self, viewer):
        super().__init__()
        self.setMouseTracking(True)
        self.pix = None
        self.rects = []
        self.start_point = None
        self.end_point = None
        self.drawing = False
        self.callback = None
        self.coord_label = None

        self.scale_factor = 1.0
        self.offset = QPoint(0, 0)
        self.selected_rect_index = -1  # 选中的 bbox
        self.viewer = viewer  # Store the JSONViewer reference
        self.setAlignment(Qt.AlignCenter)

        self.hover_index = -1
        self.selected_index = -1
        self.dragging = False
        self.drag_offset = QPointF()
        self.edit_mode = False

        self.panning = False
        self.pan_offset = QPoint(0, 0)
        self.last_pan_pos = QPoint()

    def setPixmap(self, pix):
        self.pix = pix
        self.scale_factor = min(self.width() / pix.width(), self.height() / pix.height())
        self.update()

    def start_drawing(self, callback):
        self.drawing = True
        self.callback = callback

    def stop_drawing(self):
        self.drawing = False
        self.callback = None

    def to_image_pos(self, pos):
        painter_pos = pos + self.pan_offset
        return QPointF(painter_pos.x() / self.scale_factor, painter_pos.y() / self.scale_factor)

    def mouseMoveEvent(self, event):
        if self.coord_label:
            # Convert widget coordinates to image coordinates
            image_pos = self.to_image_pos(event.pos())
            self.coord_label.setText(f"Image X: {image_pos.x():.1f}, Y: {image_pos.y():.1f}")

        if self.edit_mode:
            self.hover_index = -1
            for i, (rect, label) in enumerate(self.rects):
                scaled_rect = QRectF(
                    rect.x() * self.scale_factor,
                    rect.y() * self.scale_factor,
                    rect.width() * self.scale_factor,
                    rect.height() * self.scale_factor
                )
                if scaled_rect.contains(event.pos()):
                    self.hover_index = i
                    break

            # ✅ 节流处理，只在移动一定距离后再 update()
            if self.dragging and self.selected_index != -1:
                if not hasattr(self, 'last_mouse_pos'):
                    self.last_mouse_pos = event.pos()
                elif (event.pos() - self.last_mouse_pos).manhattanLength() < 2:
                    return  # 鼠标动得太少，不更新
                self.last_mouse_pos = event.pos()

                image_pos = self.to_image_pos(event.pos()) - self.drag_offset
                old_rect, label = self.rects[self.selected_index]
                new_rect = QRectF(image_pos.x(), image_pos.y(), old_rect.width(), old_rect.height())
                self.rects[self.selected_index] = (new_rect, label)
                self.update()
                return
        # Handle drawing mode
        elif self.drawing and self.start_point:
            self.end_point = self.to_image_pos(event.pos())
            print(f"Dragging to {self.end_point}")
            self.update()
            pass

        elif self.panning:
            delta_widget = event.pos() - self.last_pan_pos
            delta_image = QPointF(delta_widget) / self.scale_factor
            self.pan_offset -= delta_image  # Subtract for standard panning behavior
            self.last_pan_pos = event.pos()
            self.update()

    def mousePressEvent(self, event):
        if self.edit_mode:
            self.hover_index = -1
            clicked_inside_box = False

            for i, (rect, label) in enumerate(self.rects):
                scaled_rect = QRectF(
                    rect.x() * self.scale_factor,
                    rect.y() * self.scale_factor,
                    rect.width() * self.scale_factor,
                    rect.height() * self.scale_factor
                )
                if scaled_rect.contains(event.pos()):
                    self.selected_index = i
                    clicked_inside_box = True
                    self.update()

                    if event.button() == Qt.RightButton:
                        from PyQt5.QtWidgets import QMenu, QInputDialog
                        menu = QMenu(self)
                        move_action = menu.addAction("Move")
                        delete_action = menu.addAction("Delete")
                        edit_label_action = menu.addAction("Change Label")
                        action = menu.exec_(self.mapToGlobal(event.pos()))

                        if action == delete_action:
                            del self.rects[i]
                            self.selected_index = -1
                            self.update()
                            return

                        elif action == move_action:
                            self.dragging = True
                            self.drag_offset = self.to_image_pos(event.pos()) - rect.topLeft()
                            return

                        elif action == edit_label_action:
                            if hasattr(self.viewer, 'class_names'):
                                existing_classes = self.viewer.class_names
                            else:
                                existing_classes = [lbl for _, lbl in self.rects]

                            new_label, ok = QInputDialog.getText(
                                self, "Edit Label", "Enter new class name:",
                                QLineEdit.Normal, label
                            )
                            if ok and new_label:
                                new_label = new_label.strip()
                                if new_label not in existing_classes:
                                    # Append new class to classes.txt
                                    classes_path = os.path.join(self.viewer.last_open_dir, "classes.txt")
                                    with open(classes_path, "a") as f:
                                        f.write(f"{new_label}\n")
                                    # Update viewer class_names
                                    self.viewer.class_names.append(new_label)
                                    self.viewer.class_list_widget.addItem(new_label)
                                self.rects[i] = (rect, new_label)
                                self.update()
                            return

                    elif event.button() == Qt.LeftButton:
                        self.dragging = True
                        self.drag_offset = self.to_image_pos(event.pos()) - rect.topLeft()
                        return

            if not clicked_inside_box:
                self.selected_index = -1
                self.update()
            pass
        elif self.drawing and event.button() == Qt.LeftButton:
            self.start_point = self.to_image_pos(event.pos())
            self.end_point = self.start_point
            print(f"Started drawing at {self.start_point}")
            self.update()
            pass
        else:
            # Start panning if neither edit nor drawing mode is active
            if event.button() == Qt.LeftButton:
                self.panning = True
                self.last_pan_pos = event.pos()
                self.update()

    def mouseDoubleClickEvent(self, event):
        if self.edit_mode and self.selected_index != -1:
            # 用户双击，结束编辑，取消选中状态
            self.selected_index = -1
            self.update()

    def mouseReleaseEvent(self, event):
        # Handle dragging (e.g., moving an existing rectangle in edit mode)
        if self.dragging:
            self.dragging = False
            return

        # Handle drawing (finalize a new rectangle in create mode)
        if self.drawing and self.start_point and self.end_point:
            rect = QRectF(self.start_point, self.end_point).normalized()
            if rect.width() < 3 or rect.height() < 3:
                # Discard if too small
                self.start_point = None
                self.end_point = None
                self.update()
                return
            if self.callback:
                self.callback(rect)  # Pass to external handler for labeling
            # Clear drawing state
            self.start_point = None
            self.end_point = None
            self.update()

        # Handle panning (stop panning when mouse is released)
        elif self.panning:
            self.panning = False

    def paintEvent(self, event):
        if not self.pix:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Apply pan offset for panning
        painter.translate(-self.pan_offset.x(), -self.pan_offset.y())

        # Draw the image
        scaled_w = int(self.pix.width() * self.scale_factor)
        scaled_h = int(self.pix.height() * self.scale_factor)
        painter.drawPixmap(0, 0, scaled_w, scaled_h, self.pix)

        # Draw existing bounding boxes
        for i, (rect, label) in enumerate(self.rects):
            if i == self.hover_index:
                painter.setPen(QPen(QColor(255, 255, 0), 2, Qt.DashLine))
            elif i == self.selected_index:
                painter.setPen(QPen(QColor(0, 255, 255), 2))
            else:
                painter.setPen(QPen(QColor(255, 0, 0), 2))
            scaled_rect = QRectF(
                rect.x() * self.scale_factor,
                rect.y() * self.scale_factor,
                rect.width() * self.scale_factor,
                rect.height() * self.scale_factor
            )
            painter.drawRect(scaled_rect)
            painter.drawText(scaled_rect.topLeft() + QPointF(2, -4), label)

        # Draw temporary rectangle during drawing mode
        if self.drawing and self.start_point and self.end_point:
            painter.setPen(Qt.red)
            # Convert image coordinates to widget coordinates with pan offset
            start_widget_x = self.start_point.x() * self.scale_factor
            start_widget_y = self.start_point.y() * self.scale_factor
            end_widget_x = self.end_point.x() * self.scale_factor
            end_widget_y = self.end_point.y() * self.scale_factor
            # Adjust for pan offset in widget coordinates
            temp_rect = QRectF(
                start_widget_x - self.pan_offset.x(),
                start_widget_y - self.pan_offset.y(),
                end_widget_x - start_widget_x,
                end_widget_y - start_widget_y
            ).normalized()
            painter.drawRect(temp_rect)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.scale_factor *= 1.1
        else:
            self.scale_factor /= 1.1
        self.update()

    def resizeEvent(self, event):
        if not self.pix:
            return
        self.scale_factor = min(
            self.width() / self.pix.width(),
            self.height() / self.pix.height()
        )
        self.update()

    def on_textbox_focus(self, event):
        self.in_search_mode = True
        print("Entered search mode")
        event.accept()

    def search_image_by_name(self):
        if not self.in_search_mode or not self.image_files or not self.txt_name.text():
            return
        keyword = self.txt_name.text().strip().lower()
        if not keyword:
            return
        try:
            for i, name in enumerate(self.image_files):
                if keyword in os.path.basename(name).lower():
                    self.current_index = i
                    self.load_image()
                    self.txt_name.setText(os.path.basename(name))  # Update textbox with exact filename
                    self.in_search_mode = False  # Exit search mode after successful search
                    self.txt_name.clearFocus()  # Remove focus to exit search mode
                    return
            QMessageBox.warning(self, "Not Found", f"No image matching '{keyword}' found.")
            self.in_search_mode = False  # Exit search mode after failed search
            self.txt_name.clearFocus()  # Remove focus to exit search mode
        except Exception as e:
            print(f"Search error: {e}")
            self.in_search_mode = False  # Ensure exit from search mode
            self.txt_name.clearFocus()

    def save_annotations(self):
        self.save_yolo_format()  # Corrected method name

    def set_mode(self, mode):
        if mode == 'create':
            self.image_display.drawing = True
            self.image_display.edit_mode = False
            self.btn_create.setStyleSheet("background-color: lightgreen;")
            self.btn_edit.setStyleSheet("")
        elif mode == 'edit':
            self.image_display.drawing = False
            self.image_display.edit_mode = True
            self.btn_create.setStyleSheet("")
            self.btn_edit.setStyleSheet("background-color: lightgreen;")
        else:
            self.image_display.drawing = False
            self.image_display.edit_mode = False
            self.btn_create.setStyleSheet("")
            self.btn_edit.setStyleSheet("")

    def toggle_create_mode(self):
        if self.image_display.drawing:
            # 关闭创建模式
            self.image_display.drawing = False
            self.btn_create.setText("Create")
            self.btn_create.setStyleSheet("")
        else:
            # 开启创建模式，同时关闭编辑模式
            self.image_display.drawing = True
            self.image_display.edit_mode = False
            self.btn_create.setText("Creating ON")
            self.btn_create.setStyleSheet("background-color: lightgreen;")

            self.btn_edit.setText("Edit")  # 重置编辑按钮
            self.btn_edit.setStyleSheet("")

    def toggle_edit_mode(self):
        if self.image_display.edit_mode:
            # 关闭编辑模式
            self.image_display.edit_mode = False
            self.btn_edit.setText("Edit")
            self.btn_edit.setStyleSheet("")
        else:
            # 开启编辑模式，关闭创建模式
            self.image_display.edit_mode = True
            self.image_display.drawing = False
            self.btn_edit.setText("Editing ON")
            # 不高亮 edit
            self.btn_edit.setStyleSheet("")

            self.btn_create.setText("Create")  # 重置 create 按钮
            self.btn_create.setStyleSheet("")

    def on_rect_created(self, rect):
        # ✅ 创建完后询问 label（或不询问）
        if hasattr(self, 'class_names'):
            label, ok = QInputDialog.getItem(self, "Select Label", "Class:", self.class_names, 0, False)
            if ok:
                self.image_display.rects.append((rect, label))
                self.image_display.update()
        else:
            # 默认标签
            self.image_display.rects.append((rect, "unlabeled"))
            self.image_display.update()

        self.needs_save = True

    def load_last_path(self):
        config_path = "config_path.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f).get("last_open_dir", os.getcwd())
            except Exception as e:
                print("读取配置失败:", e)
        return os.getcwd()

    def load_class_list(self, path):
        if os.path.exists(path):
            with open(path, "r") as f:
                self.class_names = [line.strip() for line in f if line.strip()]
                self.class_list_widget.addItems(self.class_names)


class JSONViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Labelimg Yolo Editor")

        self.class_names = []
        self.image_files = []
        self.current_index = -1
        self.needs_save = False
        self.last_open_dir = self.load_last_path()
        self.in_search_mode = False  # Flag for search mode

        # 总体布局
        self.splitter = QSplitter()
        self.setCentralWidget(self.splitter)

        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout()
        self.left_panel.setLayout(self.left_layout)

        self.tab_widget = QTabWidget()
        self.tab_widget.setFixedWidth(320)
        self.left_layout.addWidget(self.tab_widget)

        self.tab1 = QWidget()
        self.tab1_layout = QVBoxLayout()
        self.tab1.setLayout(self.tab1_layout)
        self.tab_widget.addTab(self.tab1, "Record Viewer")

        self.tab2 = QWidget()
        self.tab2_layout = QVBoxLayout()
        self.tab2.setLayout(self.tab2_layout)
        self.tab_widget.addTab(self.tab2, "BBox Editor")

        for i in range(6):
            label = QLabel(f"field{i + 1}")
            field = QTextEdit()
            hbox = QHBoxLayout()
            hbox.addWidget(label)
            hbox.addWidget(field)
            self.tab1_layout.addLayout(hbox)

        self.tab1_layout.addWidget(QPushButton("PREV rec"))
        self.tab1_layout.addWidget(QPushButton("NEXT rec"))
        self.tab1_layout.addWidget(QPushButton("Save All recs"))

        self.class_list_widget = QListWidget()
        self.tab2_layout.addWidget(QLabel("Class List"))
        self.tab2_layout.addWidget(self.class_list_widget)

        self.btn_create = QPushButton("Create", self)
        self.btn_create.clicked.connect(self.toggle_create_mode)

        self.btn_edit = QPushButton("Edit", self)
        self.btn_edit.clicked.connect(self.toggle_edit_mode)

        self.btn_save = QPushButton("Save YOLO", self)
        self.btn_save.clicked.connect(self.save_annotations)

        self.tab2_layout.addWidget(self.btn_create)
        self.tab2_layout.addWidget(self.btn_edit)
        self.tab2_layout.addWidget(self.btn_save)

        self.right_panel = QWidget()
        self.right_layout = QVBoxLayout()
        self.right_panel.setLayout(self.right_layout)

        # 顶部图片导航区
        top_bar = QHBoxLayout()
        self.btn_folder = QPushButton("Image Folder")
        self.btn_folder.clicked.connect(self.select_folder)
        self.txt_name = QLineEdit()
        self.txt_name.setEnabled(True)
        self.txt_name.returnPressed.connect(self.search_image_by_name)
        self.txt_name.focusInEvent = self.on_textbox_focus
        
        # Add resolution display label
        self.resolution_label = QLabel("Resolution: --")
        self.resolution_label.setStyleSheet("background-color: #f0f0f0; padding: 2px 6px; border: 1px solid #ccc;")
        
        self.btn_prev = QPushButton("PREV IMAGE")
        self.btn_next = QPushButton("NEXT IMAGE")
        top_bar.addWidget(self.btn_folder)
        top_bar.addWidget(self.txt_name)
        top_bar.addWidget(self.resolution_label)  # Add resolution label to top bar
        top_bar.addWidget(self.btn_prev)
        top_bar.addWidget(self.btn_next)
        self.right_layout.addLayout(top_bar)

        # 图像显示区域 kua1
        self.image_display = ZoomableLabel(self)
        self.image_display.setStyleSheet("background: #ddd")
        self.image_display.setMinimumSize(1000, 750)  # kua3--Change image display size:
        self.image_display.callback = self.on_rect_created
        self.right_layout.addWidget(self.image_display)

        # 底部坐标显示
        self.coord_label = QLabel("X: 0.00, Y: 0.00")
        self.right_layout.addWidget(self.coord_label)
        self.image_display.coord_label = self.coord_label

        self.right_layout.addWidget(self.coord_label)
        self.info_textbox = QTextEdit()
        self.info_textbox.setReadOnly(True)
        self.info_textbox.setMaximumHeight(200)
        self.right_layout.addWidget(self.info_textbox)
        self.image_display.coord_label = self.coord_label

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setSizes([300, 900])  # kua2 Adjusted to give more space to the right panel

        # Set the rects_changed callback after image_display is initialized
        self.image_display.rects_changed = lambda: setattr(self, 'needs_save', True)

        self.btn_create.clicked.connect(self.enter_create_mode)
        self.btn_save.clicked.connect(self.save_yolo_format)
        self.btn_prev.clicked.connect(self.prev_image)
        self.btn_next.clicked.connect(self.next_image)

        self.set_mode('')
        self.setMinimumSize(1300, 1000)
        print("Window initialized, ready for interaction")

    def on_textbox_focus(self, event):
        self.in_search_mode = True
        print("Entered search mode")
        event.accept()

    def search_image_by_name(self):
        if not self.in_search_mode or not self.image_files or not self.txt_name.text():
            return
        keyword = self.txt_name.text().strip().lower()
        if not keyword:
            return
        try:
            for i, name in enumerate(self.image_files):
                if keyword in os.path.basename(name).lower():
                    self.current_index = i
                    self.load_image()
                    self.txt_name.setText(os.path.basename(name))
                    self.in_search_mode = False
                    self.txt_name.clearFocus()
                    return
            QMessageBox.warning(self, "Not Found", f"No image matching '{keyword}' found.")
            self.in_search_mode = False
            self.txt_name.clearFocus()
        except Exception as e:
            print(f"Search error: {e}")
            self.in_search_mode = False
            self.txt_name.clearFocus()

    def save_annotations(self):
        self.save_yolo_format()

    def set_mode(self, mode):
        if mode == 'create':
            self.image_display.drawing = True
            self.image_display.edit_mode = False
            self.btn_create.setStyleSheet("background-color: lightgreen;")
            self.btn_edit.setStyleSheet("")
        elif mode == 'edit':
            self.image_display.drawing = False
            self.image_display.edit_mode = True
            self.btn_create.setStyleSheet("")
            self.btn_edit.setStyleSheet("background-color: lightgreen;")
        else:
            self.image_display.drawing = False
            self.image_display.edit_mode = False
            self.btn_create.setStyleSheet("")
            self.btn_edit.setStyleSheet("")

    def toggle_create_mode(self):
        if self.image_display.drawing:
            self.image_display.drawing = False
            self.btn_create.setText("Create")
            self.btn_create.setStyleSheet("")
        else:
            self.image_display.drawing = True
            self.image_display.edit_mode = False
            self.btn_create.setText("Creating ON")
            self.btn_create.setStyleSheet("background-color: lightgreen;")
            self.btn_edit.setText("Edit")
            self.btn_edit.setStyleSheet("")

    def toggle_edit_mode(self):
        if self.image_display.edit_mode:
            self.image_display.edit_mode = False
            self.btn_edit.setText("Edit")
            self.btn_edit.setStyleSheet("")
        else:
            self.image_display.edit_mode = True
            self.image_display.drawing = False
            self.btn_edit.setText("Editing ON")
            self.btn_edit.setStyleSheet("")
            self.btn_create.setText("Create")
            self.btn_create.setStyleSheet("")

    def on_rect_created(self, rect):
        if hasattr(self, 'class_names'):
            label, ok = QInputDialog.getItem(self, "Select Label", "Class:", self.class_names, 0, False)
            if ok:
                self.image_display.rects.append((rect, label))
                self.image_display.update()
        else:
            self.image_display.rects.append((rect, "unlabeled"))
            self.image_display.update()
        self.needs_save = True

    def load_last_path(self):
        config_path = "config_path.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f).get("last_open_dir", os.getcwd())
            except Exception as e:
                print("读取配置失败:", e)
        return os.getcwd()

    def load_class_list(self, path):
        if os.path.exists(path):
            with open(path, "r") as f:
                self.class_names = [line.strip() for line in f if line.strip()]
                self.class_list_widget.addItems(self.class_names)

    def select_folder(self):
        print("select_folder called")
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", self.last_open_dir)
        if folder:
            self.last_open_dir = folder
            self.image_files = [os.path.join(folder, f) for f in os.listdir(folder)
                                if f.lower().endswith((".png", ".jpg", ".jpeg"))]
            classes_path = os.path.join(self.last_open_dir, "classes.txt")
            self.load_class_list(classes_path)
            self.image_files.sort()
            self.current_index = 0
            self.load_image()
            try:
                with open("config_path.json", "w") as f:
                    json.dump({"last_open_dir": folder}, f)
            except Exception as e:
                print("❌ Failed to save path:", e)

    def load_image(self):
        if not (0 <= self.current_index < len(self.image_files)):
            print("Invalid index or no images loaded")
            return

        # Clear the info textbox when loading a new image
        self.info_textbox.clear()

        path = self.image_files[self.current_index]
        self.txt_name.setText(os.path.basename(path))
        print(f"Loading image: {path}")

        try:
            img = load_image_correct_orientation(path)
            if img.isNull():
                raise ValueError("Loaded image is null")
            pixmap = QPixmap.fromImage(img)
            if pixmap.isNull():
                raise ValueError("Failed to convert QImage to QPixmap")
            self.image_display.setPixmap(pixmap)
            
            # Update resolution display
            w, h = img.width(), img.height()
            self.resolution_label.setText(f"Resolution: {w}×{h}")
            
            print(f"Image loaded successfully: {img.width()}x{img.height()}")
        except Exception as e:
            print(f"Error loading image {path}: {e}")
            self.image_display.setPixmap(QPixmap())
            self.image_display.rects.clear()
            self.image_display.update()
            self.resolution_label.setText("Resolution: --")  # Reset resolution on error
            return

        self.image_display.rects.clear()

        w, h = img.width(), img.height()
        txt_path = os.path.splitext(path)[0] + ".txt"
        if os.path.exists(txt_path):
            try:
                with open(txt_path, "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) == 5:
                            cls_id, cx, cy, ww, hh = map(float, parts)
                            cls_id = int(cls_id)
                            if 0 <= cls_id < len(self.class_names):
                                x = (cx - ww / 2) * w    # Convert normalized to pixel coordinates
                                y = (cy - hh / 2) * h    # Convert normalized to pixel coordinates
                                rect = QRectF(x, y, ww * w, hh * h)    # Convert normalized to pixel coordinates # Rectangle in pixel coordinates
                                label = self.class_names[cls_id]
                                self.image_display.rects.append((rect, label))
                                self.info_textbox.append(f"Loaded annotation: class={label}, rect={rect}")
                                print(f"Loaded annotation: class={label}, rect={rect}")
                            else:
                                self.info_textbox.append(f"Warning: Invalid class ID {cls_id} in {txt_path}, skipping.")
                                print(f"Warning: Invalid class ID {cls_id} in {txt_path}, skipping.")
                        else:
                            self.info_textbox.append(f"Warning: Malformed line in {txt_path}: {line}")
                            print(f"Warning: Malformed line in {txt_path}: {line}")
            except Exception as e:
                self.info_textbox.append(f"Error reading annotations from {txt_path}: {e}")
                print(f"Error reading annotations from {txt_path}: {e}")
        else:
            self.info_textbox.append(f"Info: No annotation file found at {txt_path}")
            print(f"Info: No annotation file found at {txt_path}")

        self.class_list_widget.clear()
        for _, label in self.image_display.rects:
            self.class_list_widget.addItem(label)

        self.image_display.update()
        print(f"Image and annotations loaded, rects count: {len(self.image_display.rects)}")

    def prev_image(self):
        if self.current_index > 0:
            if self.needs_save:
                self.save_yolo_format()
                self.needs_save = False
            self.current_index -= 1
            self.load_image()

    def next_image(self):
        if self.current_index < len(self.image_files) - 1:
            if self.needs_save:
                self.save_yolo_format()
                self.needs_save = False
            self.current_index += 1
            self.load_image()

    def enter_create_mode(self):
        self.image_display.start_drawing(self.handle_new_rect)

    def handle_new_rect(self, rect):
        if not self.class_names:
            return
        label, ok = QInputDialog.getItem(self, "Select Label", "Class:", self.class_names, 0, False)
        if ok:
            self.image_display.rects.append((rect, label))
            self.image_display.update()

    def save_yolo_format(self):
        if not (self.image_files and 0 <= self.current_index < len(self.image_files)):
            return
        confirm = QMessageBox.question(
            self, "Confirm Save",
            "Do you want to save current annotations?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        path = self.image_files[self.current_index]
        img = QPixmap(path)
        w, h = img.width(), img.height()
        save_path = os.path.splitext(path)[0] + ".txt"
        
        # Save annotations in YOLO format (normalized coordinates 0-1)
        with open(save_path, "w") as f:
            for rect, label in self.image_display.rects:
                class_id = self.class_names.index(label)
                
                # Convert pixel coordinates to normalized YOLO format (0-1 range)--kua4
                x = (rect.x() + rect.width() / 2) / w  # center x (normalized)
                y = (rect.y() + rect.height() / 2) / h  # center y (normalized)
                ww = rect.width() / w  # width (normalized)
                hh = rect.height() / h  # height (normalized)
                
                # Write in YOLO format: class_id center_x center_y width height
                f.write(f"{class_id} {x:.6f} {y:.6f} {ww:.6f} {hh:.6f}\n")
                
                # Debug info: show both pixel and normalized values
                self.info_textbox.append(f"Saved: class={label} (ID:{class_id})")
                self.info_textbox.append(f"  Pixel: x={rect.x():.1f}, y={rect.y():.1f}, w={rect.width():.1f}, h={rect.height():.1f}")
                self.info_textbox.append(f"  Normalized: x={x:.6f}, y={y:.6f}, w={ww:.6f}, h={hh:.6f}")
        
        print(f"[SAVE] {save_path} - Saved in YOLO normalized format")
        self.needs_save = False

    def enter_edit_mode(self):
        self.image_display.edit_mode = not self.image_display.edit_mode
        self.btn_edit.setText("Editing ON" if self.image_display.edit_mode else "Edit Mode")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    viewer = JSONViewer()
    viewer.show()
    sys.exit(app.exec_())
