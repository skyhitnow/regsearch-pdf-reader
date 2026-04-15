import sys
import re
import fitz  # PyMuPDF
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QListWidget, QLabel, 
                             QScrollArea, QFileDialog, QMessageBox, QSplitter, 
                             QSlider, QComboBox, QInputDialog, QLineEdit)
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QSettings

class PDFReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.doc = None
        self.current_page = 0
        self.zoom_factor = 1.5
        self.search_results_data = [] 
        # self.settings = QSettings("MyRegexPDFApp", "PDFReaderPresets") 

        self.settings = QSettings("pdfsearch-config.ini", QSettings.IniFormat)
        
        self.initUI()

    def initUI(self):
        self.setWindowTitle('正则搜索 PDF 阅读器')
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

        # 预设选择区域
        left_layout.addWidget(QLabel('正则表达式预设:'))
        
        search_nav_layout = QHBoxLayout()
        self.search_combo = QComboBox()
        # 只要用户点击下拉菜单并选择了某一项，就会触发（即使选的是同一项）
        self.search_combo.activated.connect(self.on_preset_selected)
        # 防止代码其他地方用 setCurrentIndex 更改时没触发
        self.search_combo.currentIndexChanged.connect(self.on_preset_selected)
        search_nav_layout.addWidget(self.search_combo, 4)
        
        self.btn_add_preset = QPushButton('添加')
        self.btn_add_preset.clicked.connect(self.add_preset)
        search_nav_layout.addWidget(self.btn_add_preset, 1)

        self.btn_del_preset = QPushButton('删除')
        self.btn_del_preset.clicked.connect(self.delete_preset)
        search_nav_layout.addWidget(self.btn_del_preset, 1)
        
        left_layout.addLayout(search_nav_layout)

        # 【关键修改 1】：将只读 Label 改为可编辑的 QLineEdit
        regex_input_layout = QHBoxLayout()
        regex_input_layout.addWidget(QLabel('当前正则:'))
        self.current_regex_input = QLineEdit()
        self.current_regex_input.setPlaceholderText('请选择预设或直接输入正则...')
        # 按回车键也可以直接搜索
        self.current_regex_input.returnPressed.connect(self.perform_search)
        regex_input_layout.addWidget(self.current_regex_input)
        left_layout.addLayout(regex_input_layout)

        # 【已修复】：去掉了自定义背景色，恢复原生样式
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
        self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(self.prev_page)
        self.page_label = QLabel('第 0 / 0 页')
        self.btn_next = QPushButton('下一页')
        self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(self.next_page)
        toolbar.addWidget(self.btn_prev)
        toolbar.addWidget(self.page_label)
        toolbar.addWidget(self.btn_next)
        toolbar.addStretch(1) 
        toolbar.addWidget(QLabel('缩放:'))
        
        # 【已修复】：标准的 QSlider 初始化方式
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(5, 40)
        self.zoom_slider.setValue(15)
        self.zoom_slider.setFixedWidth(150)
        self.zoom_slider.valueChanged.connect(self.handle_zoom)
        toolbar.addWidget(self.zoom_slider)
        
        right_layout.addLayout(toolbar)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: #525659; border: none; }")
        self.image_label = QLabel('未加载页面')
        self.image_label.setAlignment(Qt.AlignCenter)
        self.scroll_area.setWidget(self.image_label)
        right_layout.addWidget(self.scroll_area)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 850])

        self.load_presets()

    # 【已修复】：补全缺失的 open_file 函数
    def open_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, '打开 PDF', '', 'PDF Files (*.pdf)')
        if filename:
            try:
                if self.doc: 
                    self.doc.close()
                self.doc = fitz.open(filename)
                self.current_page = 0
                self.results_list.clear()
                self.result_count_label.setText('共 0 条匹配结果')
                self.update_nav_buttons()
                self.display_page(self.current_page)
                self.setWindowTitle(f'正则 PDF 阅读器 - {filename}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'无法打开文件: {str(e)}')

    def load_presets(self):
        self.search_combo.clear()
        presets = self.settings.value("regex_presets", [])
        for p in presets:
            self.search_combo.addItem(p['name'], p['regex'])
        
        if self.search_combo.count() == 0:
            self.current_regex_input.clear()

    def save_presets(self):
        presets = []
        for i in range(self.search_combo.count()):
            presets.append({
                "name": self.search_combo.itemText(i),
                "regex": self.search_combo.itemData(i)
            })
        self.settings.setValue("regex_presets", presets)

    def add_preset(self):
        name, ok1 = QInputDialog.getText(self, '添加预设', '请输入预设名称 (如: 电话号码):')
        if not (ok1 and name): return
        
        regex, ok2 = QInputDialog.getText(self, '添加预设', f'请输入 "{name}" 对应的正则表达式:')
        if not (ok2 and regex): return

        try:
            re.compile(regex)
        except re.error:
            QMessageBox.critical(self, '错误', '正则表达式语法无效！')
            return

        self.search_combo.addItem(name, regex)
        self.save_presets()
        self.search_combo.setCurrentIndex(self.search_combo.count() - 1)

    def delete_preset(self):
        idx = self.search_combo.currentIndex()
        if idx < 0: return
        
        name = self.search_combo.itemText(idx)
        # 【已修复】：QMessageBox.Yes 和 QMessageBox.No 大小写修正
        reply = QMessageBox.question(self, '确认删除', f'确定要删除预设 "{name}" 吗？',
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.search_combo.removeItem(idx)
            self.save_presets()

    # 【关键修改 2】：当在下拉菜单选中预设时，自动把正则填入输入框，等待用户检查或修改
    def on_preset_selected(self, index):
        if index >= 0:
            regex = self.search_combo.itemData(index)
            if regex:  # 确保数据不为空
                self.current_regex_input.setText(str(regex))
        else:
            self.current_regex_input.clear()

    def perform_search(self):
        if not self.doc:
            QMessageBox.warning(self, '提示', '请先打开 PDF 文件')
            return
        
        # 【关键修改 3】：完全抛弃下拉菜单的隐藏数据，一切搜索以文本框里当前的文字为准
        pattern = self.current_regex_input.text()
        if not pattern:
            QMessageBox.warning(self, '提示', '请输入正则表达式')
            return

        try:
            regex = re.compile(pattern)
        except re.error as e:
            QMessageBox.warning(self, '正则语法错误', str(e))
            return
        
        self.results_list.blockSignals(True)
        self.results_list.clear()
        self.search_results_data.clear()
        self.results_list.blockSignals(False)

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            text = page.get_text("text")
            occurrences = {}
            for match in regex.finditer(text):
                m_str = match.group()
                occurrences[m_str] = occurrences.get(m_str, 0)
                occ_idx = occurrences[m_str]
                occurrences[m_str] += 1
                
                snippet = text[max(0, match.start()-10):min(len(text), match.end()+10)].replace('\n', ' ')
                self.results_list.addItem(f"P{page_num+1}: ...{snippet}...")
                self.search_results_data.append({'page': page_num, 'str': m_str, 'occ_idx': occ_idx})

        self.result_count_label.setText(f'共找到 {len(self.search_results_data)} 条匹配结果')
        if self.search_results_data:
            self.results_list.setCurrentRow(0)
        else:
            QMessageBox.information(self, '提示', '未找到匹配项。')

    def on_result_change(self, current, previous):
        if current is None: return
        index = self.results_list.row(current)
        active_data = self.search_results_data[index]
        self.current_page = active_data['page']
        self.display_page(active_data['page'], active_data=active_data)

    def display_page(self, page_num, active_data=None):
        if not self.doc: return
        page = self.doc[page_num]
        self.update_nav_buttons()
        temp_annots = []
        target_scroll_y = 0
        page_results = [res for res in self.search_results_data if res['page'] == page_num]
        unique_strs = set(res['str'] for res in page_results)
        for m_str in unique_strs:
            rects = page.search_for(m_str)
            for i, rect in enumerate(rects):
                is_active = (active_data and active_data['str'] == m_str and active_data['occ_idx'] == i)
                if is_active: target_scroll_y = int(rect.y0 * self.zoom_factor) - 100
                annot = page.add_highlight_annot(rect)
                annot.set_colors(stroke=(1.0, 0.4, 0.0) if is_active else (1.0, 1.0, 0.0))
                annot.update()
                temp_annots.append(annot)
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom_factor, self.zoom_factor))
        for a in temp_annots: page.delete_annot(a)
        qimage = QImage(pix.samples, pix.width, pix.height, pix.stride, 
                        QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888).copy()
        self.image_label.setPixmap(QPixmap.fromImage(qimage))
        self.scroll_area.verticalScrollBar().setValue(max(0, target_scroll_y) if active_data else 0)

    def update_nav_buttons(self):
        if not self.doc: return
        self.btn_prev.setEnabled(self.current_page > 0)
        self.btn_next.setEnabled(self.current_page < len(self.doc) - 1)
        self.page_label.setText(f'第 {self.current_page + 1} / {len(self.doc)} 页')

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.display_page(self.current_page)

    def next_page(self):
        if self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.display_page(self.current_page)

    def handle_zoom(self, value):
        self.zoom_factor = value / 10.0
        self.display_page(self.current_page)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    viewer = PDFReader()
    viewer.show()
    sys.exit(app.exec_())