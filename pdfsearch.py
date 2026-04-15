import sys
import re
import os
import fitz  # PyMuPDF
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QListWidget, QLabel, 
                             QScrollArea, QFileDialog, QMessageBox, QSplitter, 
                             QSlider, QComboBox, QInputDialog, QLineEdit)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QSettings, QTimer

class PDFReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.doc = None
        self.current_page = 0
        self.zoom_factor = 1.5
        self.search_results_data = [] 
        self.active_data = None
        
        # 【配置】：使用本地文件存储预设，实现绿色便携化
        self.settings = QSettings("pdfsearch-config.ini", QSettings.IniFormat) 
        
        # 连续滚动状态
        self.page_labels = []   
        self.rendered_pages = set() 
        
        self.initUI()

    def initUI(self):
        self.setWindowTitle('高级正则 PDF 阅读器')
        self.resize(1200, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- 左侧面板：搜索与预设管理 ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        self.btn_open = QPushButton('打开 PDF 文件')
        self.btn_open.clicked.connect(self.open_file)
        left_layout.addWidget(self.btn_open)

        left_layout.addWidget(QLabel('正则表达式预设 (只显示名称):'))
        
        # 预设按钮组
        search_nav_layout = QHBoxLayout()
        self.search_combo = QComboBox()
        self.search_combo.activated.connect(self.on_preset_selected)
        self.search_combo.currentIndexChanged.connect(self.on_preset_selected)
        search_nav_layout.addWidget(self.search_combo, 4)
        
        # 【恢复】：添加和删除按钮
        self.btn_add_preset = QPushButton('添加')
        self.btn_add_preset.clicked.connect(self.add_preset)
        search_nav_layout.addWidget(self.btn_add_preset, 1)

        self.btn_del_preset = QPushButton('删除')
        self.btn_del_preset.clicked.connect(self.delete_preset)
        search_nav_layout.addWidget(self.btn_del_preset, 1)
        left_layout.addLayout(search_nav_layout)

        # 当前正则编辑框
        regex_input_layout = QHBoxLayout()
        regex_input_layout.addWidget(QLabel('当前正则:'))
        self.current_regex_input = QLineEdit()
        self.current_regex_input.setPlaceholderText('请选择预设或直接输入正则...')
        self.current_regex_input.returnPressed.connect(self.perform_search)
        regex_input_layout.addWidget(self.current_regex_input)
        left_layout.addLayout(regex_input_layout)

        self.btn_search = QPushButton('执行搜索')
        self.btn_search.clicked.connect(self.perform_search)
        left_layout.addWidget(self.btn_search)

        self.result_count_label = QLabel('共 0 条匹配结果')
        left_layout.addWidget(self.result_count_label)

        self.results_list = QListWidget()
        self.results_list.currentItemChanged.connect(self.on_result_change)
        left_layout.addWidget(self.results_list)

        # --- 右侧面板：工具栏与 PDF 视图 ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        toolbar = QHBoxLayout()
        self.btn_prev = QPushButton('上一页')
        self.btn_prev.clicked.connect(self.prev_page)
        self.page_label = QLabel('第 0 / 0 页')
        self.btn_next = QPushButton('下一页')
        self.btn_next.clicked.connect(self.next_page)
        
        toolbar.addWidget(self.btn_prev)
        toolbar.addWidget(self.page_label)
        toolbar.addWidget(self.btn_next)
        
        # 页面跳转
        toolbar.addSpacing(20)
        toolbar.addWidget(QLabel('跳转到:'))
        self.page_jump_input = QLineEdit()
        self.page_jump_input.setFixedWidth(40)
        self.page_jump_input.returnPressed.connect(self.jump_to_page_from_input)
        toolbar.addWidget(self.page_jump_input)
        self.btn_jump = QPushButton('跳转')
        self.btn_jump.clicked.connect(self.jump_to_page_from_input)
        toolbar.addWidget(self.btn_jump)
        
        toolbar.addStretch(1) 
        
        # 视图模式
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["单页显示", "连续显示"])
        self.view_mode_combo.currentIndexChanged.connect(self.change_view_mode)
        toolbar.addWidget(self.view_mode_combo)
        
        # 缩放控制
        toolbar.addWidget(QLabel('缩放:'))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(5, 40)
        self.zoom_slider.setValue(15)
        self.zoom_slider.setFixedWidth(80)
        self.zoom_slider.valueChanged.connect(self.handle_zoom)
        toolbar.addWidget(self.zoom_slider)
        
        right_layout.addLayout(toolbar)
        
        # PDF 滚动显示容器
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: #525659; border: none; }")
        self.pdf_container = QWidget()
        self.pdf_layout = QVBoxLayout(self.pdf_container)
        self.pdf_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.pdf_layout.setSpacing(15)
        self.scroll_area.setWidget(self.pdf_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.on_scroll)
        right_layout.addWidget(self.scroll_area)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 850])

        self.load_presets()

    # --- 核心方法 ---

    def open_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, '打开 PDF', '', 'PDF Files (*.pdf)')
        if filename:
            try:
                if self.doc: self.doc.close()
                self.doc = fitz.open(filename)
                self.current_page = 0
                self.results_list.clear()
                self.result_count_label.setText('共 0 条匹配结果')
                self.active_data = None
                # 【恢复】：标题栏显示文件路径
                self.setWindowTitle(f'正则 PDF 阅读器 - {filename}')
                self.setup_pages_layout()
            except Exception as e:
                QMessageBox.critical(self, '错误', f'无法打开文件: {str(e)}')

    def load_presets(self):
        """从本地 .ini 加载预设"""
        self.search_combo.clear()
        presets = self.settings.value("regex_presets", [])
        if presets:
            for p in presets:
                self.search_combo.addItem(p['name'], p['regex'])
        if self.search_combo.count() == 0:
            self.current_regex_input.clear()

    def save_presets(self):
        """保存预设到本地 .ini"""
        presets = []
        for i in range(self.search_combo.count()):
            presets.append({"name": self.search_combo.itemText(i), "regex": self.search_combo.itemData(i)})
        self.settings.setValue("regex_presets", presets)

    def add_preset(self):
        name, ok1 = QInputDialog.getText(self, '添加预设', '请输入名称:')
        if not (ok1 and name): return
        regex, ok2 = QInputDialog.getText(self, '添加预设', f'请输入 "{name}" 的正则:')
        if not (ok2 and regex): return
        try:
            re.compile(regex)
        except re.error:
            QMessageBox.critical(self, '错误', '正则语法无效！')
            return
        self.search_combo.addItem(name, regex)
        self.save_presets()
        self.search_combo.setCurrentIndex(self.search_combo.count() - 1)

    def delete_preset(self):
        idx = self.search_combo.currentIndex()
        if idx < 0: return
        if QMessageBox.question(self, '确认删除', f'确定删除预设 "{self.search_combo.itemText(idx)}" 吗？',
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.search_combo.removeItem(idx)
            self.save_presets()

    def on_preset_selected(self, index):
        if index >= 0:
            regex = self.search_combo.itemData(index)
            if regex: self.current_regex_input.setText(str(regex))

    def perform_search(self):
        if not self.doc: return
        pattern = self.current_regex_input.text()
        if not pattern: return
        try:
            regex = re.compile(pattern)
        except re.error as e:
            QMessageBox.warning(self, '语法错误', str(e))
            return
        
        self.results_list.blockSignals(True)
        self.results_list.clear()
        self.search_results_data.clear()
        self.active_data = None
        self.results_list.blockSignals(False)

        for page_num in range(len(self.doc)):
            text = self.doc[page_num].get_text("text")
            occurrences = {}
            for match in regex.finditer(text):
                m_str = match.group()
                occ_idx = occurrences.get(m_str, 0)
                occurrences[m_str] = occ_idx + 1
                snippet = text[max(0, match.start()-10):min(len(text), match.end()+10)].replace('\n', ' ')
                self.results_list.addItem(f"P{page_num+1}: ...{snippet}...")
                self.search_results_data.append({'page': page_num, 'str': m_str, 'occ_idx': occ_idx})

        self.result_count_label.setText(f'共找到 {len(self.search_results_data)} 条匹配结果')
        if self.search_results_data:
            self.results_list.setCurrentRow(0)
        self.rendered_pages.clear()
        self.render_visible_pages()

    # 【恢复】：切换显示模式逻辑
    def change_view_mode(self):
        self.setup_pages_layout()
        if self.active_data:
            QTimer.singleShot(0, self.jump_to_active_result)
        else:
            self.scroll_area.verticalScrollBar().setValue(0)

    def setup_pages_layout(self):
        if not self.doc: return
        while self.pdf_layout.count():
            item = self.pdf_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.page_labels.clear()
        self.rendered_pages.clear()
        is_continuous = (self.view_mode_combo.currentIndex() == 1)
        pages_to_show = range(len(self.doc)) if is_continuous else [self.current_page]
        for p in pages_to_show:
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background-color: white;")
            rect = self.doc[p].rect
            lbl.setFixedSize(int(rect.width * self.zoom_factor), int(rect.height * self.zoom_factor))
            self.pdf_layout.addWidget(lbl)
            self.page_labels.append((p, lbl))
        self.update_nav_buttons()
        QTimer.singleShot(0, self.render_visible_pages)

    def on_scroll(self):
        self.render_visible_pages()
        self.update_current_page_by_scroll()

    def render_visible_pages(self):
        """懒加载核心"""
        if not self.doc or not self.page_labels: return
        v_top = self.scroll_area.verticalScrollBar().value()
        v_bottom = v_top + self.scroll_area.viewport().height()
        buffer = 1000 
        for p, lbl in self.page_labels:
            if lbl.y() + lbl.height() > v_top - buffer and lbl.y() < v_bottom + buffer:
                if p not in self.rendered_pages:
                    lbl.setPixmap(self.get_page_pixmap(p))
                    self.rendered_pages.add(p)
            elif p in self.rendered_pages:
                lbl.clear()
                self.rendered_pages.remove(p)

    def get_page_pixmap(self, page_num):
        page = self.doc[page_num]
        temp_annots = []
        page_results = [res for res in self.search_results_data if res['page'] == page_num]
        for res in page_results:
            rects = page.search_for(res['str'])
            if rects:
                idx = min(res['occ_idx'], len(rects) - 1)
                is_active = (self.active_data and self.active_data == res)
                annot = page.add_highlight_annot(rects[idx])
                annot.set_colors(stroke=(1.0, 0.4, 0.0) if is_active else (1.0, 1.0, 0.0))
                annot.update()
                temp_annots.append(annot)
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom_factor, self.zoom_factor))
        for a in temp_annots: page.delete_annot(a)
        fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
        return QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy())

    def update_current_page_by_scroll(self):
        if self.view_mode_combo.currentIndex() == 0 or not self.page_labels: return
        v_top = self.scroll_area.verticalScrollBar().value()
        center_y = v_top + (self.scroll_area.viewport().height() / 2)
        for p, lbl in self.page_labels:
            if lbl.y() <= center_y <= lbl.y() + lbl.height():
                if self.current_page != p:
                    self.current_page = p
                    self.update_nav_buttons()
                break

    def update_nav_buttons(self):
        if not self.doc: return
        self.btn_prev.setEnabled(self.current_page > 0)
        self.btn_next.setEnabled(self.current_page < len(self.doc) - 1)
        self.page_label.setText(f'第 {self.current_page + 1} / {len(self.doc)} 页')

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.nav_jump_to_page()

    def next_page(self):
        if self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.nav_jump_to_page()

    def jump_to_page_from_input(self):
        text = self.page_jump_input.text()
        if not text.isdigit(): return
        page_num = int(text) - 1
        if self.doc and 0 <= page_num < len(self.doc):
            self.current_page = page_num
            self.nav_jump_to_page()
            self.page_jump_input.clear()
            self.page_jump_input.clearFocus()

    def nav_jump_to_page(self):
        if self.view_mode_combo.currentIndex() == 0:
            self.setup_pages_layout()
        else:
            for p, lbl in self.page_labels:
                if p == self.current_page:
                    self.scroll_area.verticalScrollBar().setValue(lbl.y())
                    break
        self.update_nav_buttons()

    def on_result_change(self, current, previous):
        if not current: return
        self.active_data = self.search_results_data[self.results_list.row(current)]
        self.current_page = self.active_data['page']
        if self.view_mode_combo.currentIndex() == 0:
            self.setup_pages_layout()
        self.rendered_pages.clear()
        QTimer.singleShot(0, self.jump_to_active_result)

    def jump_to_active_result(self):
        if not self.active_data: return
        label_y = 0
        for p, lbl in self.page_labels:
            if p == self.active_data['page']:
                label_y = lbl.y()
                break
        rects = self.doc[self.active_data['page']].search_for(self.active_data['str'])
        if rects:
            idx = min(self.active_data['occ_idx'], len(rects)-1)
            y_in_page = int(rects[idx].y0 * self.zoom_factor)
            self.scroll_area.verticalScrollBar().setValue(label_y + y_in_page - 100)
        self.render_visible_pages()
        self.update_nav_buttons()

    def handle_zoom(self, value):
        self.zoom_factor = value / 10.0
        self.setup_pages_layout()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    viewer = PDFReader()
    viewer.show()
    sys.exit(app.exec_())