import sys
import re
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
        # self.settings = QSettings("MyRegexPDFApp", "PDFReaderPresets") 
        self.settings = QSettings("pdfsearch-config.ini", QSettings.IniFormat)
        
        self.page_labels = []   
        self.rendered_pages = set() 
        
        self.initUI()

    def initUI(self):
        self.setWindowTitle('正则 PDF 阅读器')
        self.resize(1200, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- 左侧面板 ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        self.btn_open = QPushButton('打开 PDF 文件')
        self.btn_open.clicked.connect(self.open_file)
        left_layout.addWidget(self.btn_open)

        left_layout.addWidget(QLabel('正则表达式预设:'))
        
        search_nav_layout = QHBoxLayout()
        self.search_combo = QComboBox()
        self.search_combo.activated.connect(self.on_preset_selected)
        self.search_combo.currentIndexChanged.connect(self.on_preset_selected)
        search_nav_layout.addWidget(self.search_combo, 4)
        
        left_layout.addLayout(search_nav_layout)

        regex_input_layout = QHBoxLayout()
        regex_input_layout.addWidget(QLabel('当前正则:'))
        self.current_regex_input = QLineEdit()
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

        # --- 右侧面板 ---
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
        
        # 【新增】：页面跳转功能区
        toolbar.addSpacing(20)
        toolbar.addWidget(QLabel('跳转到:'))
        self.page_jump_input = QLineEdit()
        self.page_jump_input.setFixedWidth(40)
        self.page_jump_input.setPlaceholderText('页码')
        self.page_jump_input.returnPressed.connect(self.jump_to_page_from_input)
        toolbar.addWidget(self.page_jump_input)
        
        self.btn_jump = QPushButton('跳转')
        self.btn_jump.clicked.connect(self.jump_to_page_from_input)
        toolbar.addWidget(self.btn_jump)
        
        toolbar.addStretch(1) 
        
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["单页显示", "连续显示"])
        self.view_mode_combo.currentIndexChanged.connect(self.change_view_mode)
        toolbar.addWidget(self.view_mode_combo)
        
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(5, 40)
        self.zoom_slider.setValue(15)
        self.zoom_slider.setFixedWidth(80)
        self.zoom_slider.valueChanged.connect(self.handle_zoom)
        toolbar.addWidget(self.zoom_slider)
        
        right_layout.addLayout(toolbar)
        
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

    # --- 核心跳转逻辑 ---

    def jump_to_page_from_input(self):
        """处理跳转输入框的逻辑"""
        text = self.page_jump_input.text()
        if not text.isdigit():
            return
        
        page_num = int(text) - 1  # 用户输入从1开始，内部索引从0开始
        
        if not self.doc:
            return
            
        if 0 <= page_num < len(self.doc):
            self.current_page = page_num
            self.nav_jump_to_page()
            self.page_jump_input.clear() # 跳转后清空输入框
            self.page_jump_input.clearFocus() # 失去焦点
        else:
            QMessageBox.warning(self, '错误', f'页码超出范围！请输入 1 到 {len(self.doc)} 之间的数字。')

    def nav_jump_to_page(self):
        """执行实际的跳转动作（适配单页/连续模式）"""
        if self.view_mode_combo.currentIndex() == 0:
            # 单页模式：重新构建布局，显示新的一页
            self.setup_pages_layout()
            self.scroll_area.verticalScrollBar().setValue(0)
        else:
            # 连续模式：在当前所有页面中找到目标页的 Y 坐标
            for p, lbl in self.page_labels:
                if p == self.current_page:
                    # 将滚动条直接拉到该 QLabel 的顶部
                    self.scroll_area.verticalScrollBar().setValue(lbl.y())
                    break
        self.update_nav_buttons()

    def change_view_mode(self):
        """切换单页/连续显示模式"""
        self.setup_pages_layout()
        if self.active_data:
            # 如果当前有高亮的搜索结果，切换模式后重新跳转到该结果
            QTimer.singleShot(0, self.jump_to_active_result)
        else:
            # 否则直接回到页面顶部
            self.scroll_area.verticalScrollBar().setValue(0)

    # --- 基础功能逻辑 ---

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
                
                # 👇 就是这一行！把它加回来，标题栏就会恢复显示路径和文件名了
                self.setWindowTitle(f'正则 PDF 阅读器 - {filename}')
                
                self.setup_pages_layout()
            except Exception as e:
                QMessageBox.critical(self, '错误', f'无法打开文件: {str(e)}')

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

    def load_presets(self):
        for p in self.settings.value("regex_presets", []):
            self.search_combo.addItem(p['name'], p['regex'])

    def perform_search(self):
        if not self.doc: return
        pattern = self.current_regex_input.text()
        if not pattern: return
        regex = re.compile(pattern)
        self.results_list.clear()
        self.search_results_data.clear()
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
        if self.search_results_data: self.results_list.setCurrentRow(0)

    def on_preset_selected(self, index):
        if index >= 0: self.current_regex_input.setText(str(self.search_combo.itemData(index)))

if __name__ == '__main__':
    app = QApplication(sys.argv)
    viewer = PDFReader()
    viewer.show()
    sys.exit(app.exec_())