import sys
import re
import os
import fitz  # PyMuPDF
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QListWidget, QLabel, 
                             QScrollArea, QFileDialog, QMessageBox, QSplitter, 
                             QSlider, QComboBox, QInputDialog, QLineEdit, QAction, QListWidgetItem)
from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor
from PyQt5.QtCore import Qt, QSettings

# ==========================================
# 自定义标签类：实现字符级精准文本选择
# ==========================================
class SmartTextLabel(QLabel):
    def __init__(self, doc, page_num, zoom_factor, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.page_num = page_num
        self.zoom_factor = zoom_factor
        self.chars = []
        
        # 提取底层字符坐标
        page_dict = self.doc[self.page_num].get_text("rawdict")
        for block in page_dict.get("blocks", []):
            if block.get("type") == 0:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        for c in span.get("chars", []):
                            self.chars.append({'rect': fitz.Rect(c['bbox']), 'c': c['c']})
        
        self.start_idx = None
        self.current_idx = None

    def get_closest_char_idx(self, x, y):
        if not self.chars: return None
        z = self.zoom_factor
        pdf_pos = fitz.Point(x / z, y / z)
        min_dist = float('inf')
        closest_idx = None
        for i, char_info in enumerate(self.chars):
            r = char_info['rect']
            if r.contains(pdf_pos): return i
            cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
            dist = (cx - pdf_pos.x)**2 + (cy - pdf_pos.y)**2
            if dist < min_dist:
                min_dist, closest_idx = dist, i
        return closest_idx if min_dist < 400 else None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_idx = self.get_closest_char_idx(event.pos().x(), event.pos().y())
            self.current_idx = self.start_idx
            self.update()

    def mouseMoveEvent(self, event):
        if self.start_idx is not None:
            self.current_idx = self.get_closest_char_idx(event.pos().x(), event.pos().y())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.start_idx is not None:
            self.current_idx = self.get_closest_char_idx(event.pos().x(), event.pos().y())
            if self.start_idx is not None and self.current_idx is not None:
                start, end = min(self.start_idx, self.current_idx), max(self.start_idx, self.current_idx)
                text = "".join([self.chars[i]['c'] for i in range(start, end + 1)])
                if text: QApplication.clipboard().setText(text)
            self.start_idx = self.current_idx = None
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.start_idx is not None and self.current_idx is not None:
            painter = QPainter(self)
            painter.setBrush(QColor(0, 120, 215, 100))
            painter.setPen(Qt.NoPen)
            z = self.zoom_factor
            start, end = min(self.start_idx, self.current_idx), max(self.start_idx, self.current_idx)
            for i in range(start, end + 1):
                r = self.chars[i]['rect']
                painter.drawRect(int(r.x0 * z), int(r.y0 * z), int((r.x1 - r.x0) * z), int((r.y1 - r.y0) * z))

# ==========================================
# 主程序
# ==========================================
class PDFReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.doc = None
        self.current_page = 0
        self.zoom_factor = 1.5
        self.search_results_data = []
        self.active_data = None
        self.all_bookmarks = [] 
        
        self.settings = QSettings("pdfsearch-config.ini", QSettings.IniFormat)
        self.page_labels = []
        self.rendered_pages = set()
        
        self.initUI()
        self.load_presets()
        self.load_bookmarks()

    def initUI(self):
        self.setWindowTitle('高级正则 PDF 阅读器')
        self.resize(1400, 900)

        # --- 菜单栏 ---
        menubar = self.menuBar()
        file_menu = menubar.addMenu('文件')
        
        # 1. 打开
        open_act = QAction('打开', self)
        open_act.setShortcut('Ctrl+O')
        open_act.triggered.connect(self.open_file)
        
        # 2. 【新增】直接保存
        save_act = QAction('保存', self)
        save_act.setShortcut('Ctrl+S') # 绑定 Ctrl+S 快捷键
        save_act.triggered.connect(self.direct_save)
        
        # 3. 另存为
        save_as_act = QAction('另存为', self)
        save_as_act.setShortcut('Ctrl+Shift+S')
        save_as_act.triggered.connect(self.save_as_file)
        
        # 4. 关闭
        close_act = QAction('关闭', self)
        close_act.setShortcut('Ctrl+W')
        close_act.triggered.connect(self.close_file)
        
        # 按照标准顺序添加
        file_menu.addAction(open_act)
        file_menu.addSeparator() # 加一根分割线更好看
        file_menu.addAction(save_act)
        file_menu.addAction(save_as_act)
        file_menu.addSeparator()
        file_menu.addAction(close_act)

        # 主布局使用 Splitter
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(self.main_splitter)

        # --- 1. 左侧面板：书签栏 ---
        self.bookmark_panel = QWidget()
        bookmark_layout = QVBoxLayout(self.bookmark_panel)
        bookmark_layout.addWidget(QLabel("<b>文档书签</b>"))
        
        # 【修改位置】：将添加、删除按钮移动到顶部
        bk_btn_layout = QHBoxLayout()
        self.btn_add_bk = QPushButton("添加")
        self.btn_add_bk.clicked.connect(self.add_bookmark)
        self.btn_del_bk = QPushButton("删除")
        self.btn_del_bk.clicked.connect(self.delete_bookmark)
        bk_btn_layout.addWidget(self.btn_add_bk)
        bk_btn_layout.addWidget(self.btn_del_bk)
        bookmark_layout.addLayout(bk_btn_layout)
        
        # 搜索框往下顺延
        self.bookmark_search = QLineEdit()
        self.bookmark_search.setPlaceholderText("搜索书签...")
        self.bookmark_search.textChanged.connect(self.filter_bookmarks)
        bookmark_layout.addWidget(self.bookmark_search)
        
        # 书签列表
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemClicked.connect(self.on_bookmark_clicked)
        bookmark_layout.addWidget(self.bookmark_list)

       # --- 2. 中间面板：文档预览 ---
        self.center_panel = QWidget()
        center_layout = QVBoxLayout(self.center_panel)
        
        mid_toolbar = QHBoxLayout()
        
        mid_toolbar.addStretch(1) # 左侧隐形弹簧，把翻页区往中间挤
        
        # --- 居中区域：翻页与跳转 ---
        self.btn_prev = QPushButton('上一页')
        self.btn_prev.clicked.connect(self.prev_page)
        self.page_label = QLabel('第 0 / 0 页')
        self.btn_next = QPushButton('下一页')
        self.btn_next.clicked.connect(self.next_page)
        mid_toolbar.addWidget(self.btn_prev)
        mid_toolbar.addWidget(self.page_label)
        mid_toolbar.addWidget(self.btn_next)
        
        mid_toolbar.addSpacing(20)
        mid_toolbar.addWidget(QLabel('跳转到:'))
        self.page_jump_input = QLineEdit()
        self.page_jump_input.setFixedWidth(40)
        self.page_jump_input.returnPressed.connect(self.jump_to_page_from_input)
        mid_toolbar.addWidget(self.page_jump_input)
        self.btn_jump = QPushButton('跳转')
        self.btn_jump.clicked.connect(self.jump_to_page_from_input)
        mid_toolbar.addWidget(self.btn_jump)
        
        mid_toolbar.addStretch(1) # 右侧隐形弹簧，把缩放区死死顶在最右边
        
        mid_toolbar.addStretch(1) # 右侧隐形弹簧，把缩放区死死顶在最右边
        
        # --- 靠右区域：缩放控制 ---
        mid_toolbar.addWidget(QLabel("缩放:"))
        
        # 【新增】：缩小和放大按钮
        self.btn_zoom_out = QPushButton('-')
        self.btn_zoom_out.setFixedWidth(30)
        self.btn_zoom_out.clicked.connect(self.zoom_out_step)
        mid_toolbar.addWidget(self.btn_zoom_out)
        
        self.btn_zoom_in = QPushButton('+')
        self.btn_zoom_in.setFixedWidth(30)
        self.btn_zoom_in.clicked.connect(self.zoom_in_step)
        mid_toolbar.addWidget(self.btn_zoom_in)

        # 【修改】：将滑块范围扩大10倍，支持 5% (0.05) 的精细微调
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(50, 400)  # 代表 50% 到 400%
        self.zoom_slider.setValue(150)      # 默认 150% (相当于原来的 1.5x)
        self.zoom_slider.setFixedWidth(100)
        self.zoom_slider.valueChanged.connect(self.handle_zoom)
        mid_toolbar.addWidget(self.zoom_slider)
        
        center_layout.addLayout(mid_toolbar)

        # 【重点】：这里是真正显示 PDF 的深色深渊区域，终于补回来了！
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        # self.scroll_area.setStyleSheet("background-color: #525659;")
        self.pdf_container = QWidget()
        self.pdf_layout = QVBoxLayout(self.pdf_container)
        self.pdf_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll_area.setWidget(self.pdf_container)
        
        center_layout.addWidget(self.scroll_area)

        # --- 3. 右侧面板：正则搜索 ---
        self.search_panel = QWidget()
        search_layout = QVBoxLayout(self.search_panel)
        search_layout.addWidget(QLabel("<b>正则搜索</b>"))
        
        self.search_combo = QComboBox()
        self.search_combo.currentIndexChanged.connect(self.on_preset_selected)
        search_layout.addWidget(self.search_combo)
        
        reg_btn_layout = QHBoxLayout()
        btn_add_pre = QPushButton("新增预设"); btn_add_pre.clicked.connect(self.add_preset)
        btn_del_pre = QPushButton("删除预设"); btn_del_pre.clicked.connect(self.delete_preset)
        reg_btn_layout.addWidget(btn_add_pre); reg_btn_layout.addWidget(btn_del_pre)
        search_layout.addLayout(reg_btn_layout)

        self.current_regex_input = QLineEdit()
        self.current_regex_input.setPlaceholderText("输入正则表达式...")
        # 【新增】：绑定回车键触发搜索功能
        self.current_regex_input.returnPressed.connect(self.perform_search)
        search_layout.addWidget(self.current_regex_input)
        
        self.btn_do_search = QPushButton("执行搜索")
        self.btn_do_search.clicked.connect(self.perform_search)
        search_layout.addWidget(self.btn_do_search)
        
        self.result_count_label = QLabel("共 0 条结果")
        search_layout.addWidget(self.result_count_label)
        
        self.results_list = QListWidget()
        self.results_list.currentItemChanged.connect(self.on_result_change)
        search_layout.addWidget(self.results_list)

        self.main_splitter.addWidget(self.bookmark_panel)
        self.main_splitter.addWidget(self.center_panel)
        self.main_splitter.addWidget(self.search_panel)
        self.main_splitter.setSizes([200, 800, 300])

    # --- 书签逻辑 ---
    # ==========================================
    # --- 替换以下关于书签和保存的 6 个函数 ---
    # ==========================================

    def load_bookmarks(self):
        """打开文件时，从 PDF 内部读取原生书签 (大纲/目录)"""
        self.all_bookmarks = []
        if self.doc:
            # PyMuPDF 提取的 TOC 格式为: [[层级, 标题, 页码(从1开始)], ...]
            toc = self.doc.get_toc()
            for item in toc:
                if len(item) >= 3:
                    self.all_bookmarks.append({
                        "name": str(item[1]),
                        "page": item[2] - 1  # 界面底层页码从 0 开始，需要减 1
                    })
        self.render_bookmark_list()

    def sync_bookmarks_to_doc(self):
        """将内部书签列表同步到内存中的 PDF 原生目录树"""
        if not self.doc: return
        toc = []
        for bk in self.all_bookmarks:
            # 默认将书签全部设为第一层级 (Level 1)
            toc.append([1, bk['name'], bk['page'] + 1])
        # 将目录树写回 PDF 内存对象
        self.doc.set_toc(toc)

    def add_bookmark(self):
        if not self.doc: return
        name, ok = QInputDialog.getText(self, "添加书签", "书签名称:", text=f"书签 {len(self.all_bookmarks)+1}")
        if ok and name:
            self.all_bookmarks.append({"name": name, "page": self.current_page})
            self.sync_bookmarks_to_doc() # 立刻写入内存中的 PDF 结构
            self.render_bookmark_list()

    def delete_bookmark(self):
        if not self.doc: return
        row = self.bookmark_list.currentRow()
        if row >= 0:
            original_idx = self.bookmark_list.item(row).data(Qt.UserRole)
            del self.all_bookmarks[original_idx]
            self.sync_bookmarks_to_doc() # 立刻更新内存中的 PDF 结构
            self.render_bookmark_list()

    def render_bookmark_list(self, filter_text=""):
        self.bookmark_list.clear()
        for i, bk in enumerate(self.all_bookmarks):
            if filter_text.lower() in bk['name'].lower():
                item = QListWidgetItem(f"{bk['name']} (P{bk['page']+1})")
                item.setData(Qt.UserRole, i)
                self.bookmark_list.addItem(item)


    def filter_bookmarks(self, text):
        self.render_bookmark_list(text)

    def on_bookmark_clicked(self, item):
        idx = item.data(Qt.UserRole)
        self.current_page = self.all_bookmarks[idx]['page']
        self.setup_pages_layout()

    # --- 文件操作 ---
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", "", "PDF (*.pdf)")
        if path:
            if self.doc: self.doc.close()
            self.doc = fitz.open(path)
            self.current_page = 0
            
            # 【重要】：打开文件后立刻从 PDF 读取它原生的书签
            self.load_bookmarks() 
            
            self.setup_pages_layout()
            self.setWindowTitle(f"高级正则 PDF 阅读器 - {os.path.basename(path)}")

    def close_file(self):
        """关闭当前文档并彻底清空所有相关的 UI 元素"""
        if self.doc:
            self.doc.close()
            self.doc = None
            
        # 1. 重置窗口标题和页码信息
        self.setWindowTitle('高级正则 PDF 阅读器')
        self.current_page = 0
        self.page_label.setText('第 0 / 0 页')
        if hasattr(self, 'page_jump_input'):
            self.page_jump_input.clear()

        # 2. 【核心修改】：彻底清空书签相关内容
        self.all_bookmarks = []            # 清空内部数据列表
        self.bookmark_list.clear()         # 清空左侧列表控件
        self.bookmark_search.clear()       # 清空书签搜索框

        # 3. 清空右侧正则搜索相关内容
        self.results_list.clear()
        self.result_count_label.setText('共 0 条结果')
        self.active_data = None
        
        # 4. 清空中间 PDF 渲染视图
        while self.pdf_layout.count():
            item = self.pdf_layout.takeAt(0)
            if item.widget(): 
                item.widget().deleteLater()
                
        self.page_labels.clear()
        self.rendered_pages.clear()

    def direct_save(self):
        """【直接保存】：不弹出对话框，直接将书签写入当前打开的 PDF"""
        if not self.doc:
            QMessageBox.warning(self, "提示", "当前没有打开任何文件。")
            return
            
        try:
            # 首先同步书签到内存对象
            self.sync_bookmarks_to_doc()
            
            # 使用增量保存 (incremental=True) 直接覆盖原文件
            # 这种方式速度最快，且不需要关闭文件流
            self.doc.save(self.doc.name, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            
            # 在状态栏或通过弹窗提示（为了不打断思路，这里用状态栏提示更好，如果没有状态栏就用短暂弹窗）
            QMessageBox.information(self, "保存成功", "更改已保存至原文件。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法覆盖原文件: {str(e)}\n请检查文件是否被其他程序占用。")

    def save_as_file(self):
        """【另存为】：弹出对话框选择新路径"""
        if not self.doc:
            return
            
        path, _ = QFileDialog.getSaveFileName(self, "另存为", self.doc.name, "PDF (*.pdf)")
        if path:
            try:
                self.sync_bookmarks_to_doc()
                # 另存为通常不需要增量保存，直接全量写入即可
                self.doc.save(path)
                QMessageBox.information(self, "成功", "文件已另存为新路径。")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"另存为失败: {str(e)}")

    # --- 搜索逻辑 ---
    def load_presets(self):
        self.search_combo.clear()
        presets = self.settings.value("regex_presets", [])
        for p in presets: self.search_combo.addItem(p['name'], p['regex'])

    def add_preset(self):
        # 第一步：只询问名称
        name, ok1 = QInputDialog.getText(self, '预设', '名称:')
        if not (ok1 and name.strip()): 
            return
            
        name = name.strip()
        presets = self.settings.value("regex_presets", [])
        
        # 查找是否已存在同名预设
        existing_idx = -1
        for i, p in enumerate(presets):
            if p['name'] == name:
                existing_idx = i
                break
                
        # 【核心修改】：立刻检查重名并询问
        if existing_idx >= 0:
            reply = QMessageBox.question(self, '预设已存在', 
                                         f'预设 "{name}" 已经存在。\n是否要更新它的正则表达式？',
                                         QMessageBox.Yes | QMessageBox.No, 
                                         QMessageBox.No)
            if reply != QMessageBox.Yes:
                return # 用户选择了“否”，直接中止流程，不再弹后续窗口
                
            # 用户选择“是”，提取出旧的正则准备回填
            default_reg = presets[existing_idx]['regex']
        else:
            default_reg = ""
            
        # 第二步：确认名字没问题（或确认覆盖）后，才弹出正则输入框
        # 如果是更新，旧正则会自动显示在输入框里供修改
        reg, ok2 = QInputDialog.getText(self, '预设', f'为 "{name}" 输入正则:', text=default_reg)
        if not (ok2 and reg.strip()): 
            return
            
        reg = reg.strip()
            
        # 第三步：保存逻辑
        if existing_idx >= 0:
            presets[existing_idx]['regex'] = reg
            target_idx = existing_idx
        else:
            presets.append({"name": name, "regex": reg})
            target_idx = len(presets) - 1
            
        self.settings.setValue("regex_presets", presets)
        self.load_presets()
        self.search_combo.setCurrentIndex(target_idx)

    def delete_preset(self):
        idx = self.search_combo.currentIndex()
        if idx >= 0:
            # 获取当前选中的预设名称，让提示更直观
            preset_name = self.search_combo.itemText(idx)
            
            # 弹出带警示图标的确认对话框
            reply = QMessageBox.question(self, '确认删除', 
                                         f'确定要永久删除正则预设 "{preset_name}" 吗？',
                                         QMessageBox.Yes | QMessageBox.No, 
                                         QMessageBox.No) # 默认焦点放在 "No" 上防止误按回车
            
            if reply == QMessageBox.Yes:
                presets = self.settings.value("regex_presets", [])
                del presets[idx]
                self.settings.setValue("regex_presets", presets)
                self.load_presets()

    def on_preset_selected(self, idx):
        if idx >= 0: self.current_regex_input.setText(self.search_combo.itemData(idx))

    def perform_search(self):
        if not self.doc: return
        pattern = self.current_regex_input.text()
        if not pattern: return
        try: regex = re.compile(pattern)
        except Exception as e:
            QMessageBox.warning(self, '语法错误', str(e))
            return
        
        self.results_list.clear()
        self.search_results_data.clear()
        self.active_data = None
        
        # 恢复之前完美的：底层坐标级精准跨行搜索逻辑
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            page_dict = page.get_text("rawdict")
            
            raw_chars = []
            for block in page_dict.get("blocks", []):
                if block.get("type") == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            for c in span.get("chars", []):
                                raw_chars.append((c['c'], fitz.Rect(c['bbox'])))
                        raw_chars.append(('\n', fitz.Rect(0,0,0,0)))
            
            clean_chars = []
            for i, (ch, rect) in enumerate(raw_chars):
                if ch == '\n':
                    prev_ch = raw_chars[i-1][0] if i > 0 else ' '
                    next_ch = raw_chars[i+1][0] if i < len(raw_chars)-1 else ' '
                    if (ord(prev_ch) > 255) and (ord(next_ch) > 255):
                        continue
                    else:
                        clean_chars.append((' ', rect))
                else:
                    clean_chars.append((ch, rect))
                    
            clean_text = "".join([c[0] for c in clean_chars])
            seen_rects = []
            
            for match in regex.finditer(clean_text):
                m_str = match.group()
                if not m_str: continue
                
                start_idx = match.start()
                end_idx = match.end()
                
                first_rect = None
                for r in [c[1] for c in clean_chars[start_idx:end_idx]]:
                    if r.get_area() > 0:
                        first_rect = r
                        break
                if not first_rect: continue
                
                is_duplicate = False
                for seen_rect in seen_rects:
                    if (first_rect & seen_rect).get_area() > 0.5 * first_rect.get_area():
                        is_duplicate = True
                        break
                if is_duplicate: continue
                
                seen_rects.append(first_rect)
                
                match_rects = [c[1] for c in clean_chars[start_idx:end_idx] if c[1].get_area() > 0]
                snippet = clean_text[max(0, start_idx-10):min(len(clean_text), end_idx+10)]
                
                self.results_list.addItem(f"P{page_num+1}: ...{snippet}...")
                self.search_results_data.append({
                    'page': page_num, 
                    'str': m_str, 
                    'rects': match_rects # 恢复将物理坐标存入结果
                })

        self.result_count_label.setText(f"共 {len(self.search_results_data)} 条结果")
        if self.search_results_data:
            self.results_list.setCurrentRow(0)

    def on_result_change(self, cur, prev):
        if cur:
            res = self.search_results_data[self.results_list.row(cur)]
            self.active_data = res # 必须设置 active_data，才能显示深橙色的选中状态
            self.current_page = res['page']
            self.setup_pages_layout()

    

    # --- 渲染与导航 ---
    def setup_pages_layout(self):
        if not self.doc: return
        while self.pdf_layout.count():
            w = self.pdf_layout.takeAt(0).widget()
            if w: w.deleteLater()
        self.page_labels.clear()
        
        lbl = SmartTextLabel(self.doc, self.current_page, self.zoom_factor)
        lbl.setPixmap(self.get_page_pixmap(self.current_page))
        self.pdf_layout.addWidget(lbl)
        self.page_label.setText(f"第 {self.current_page+1} / {len(self.doc)} 页")

    def get_page_pixmap(self, p_num):
        page = self.doc[p_num]
        temp_annots = []
        page_results = [res for res in self.search_results_data if res['page'] == p_num]
        
        # 恢复之前完美的：同行融合抗重叠变深渲染逻辑
        for res in page_results:
            is_active = (self.active_data and self.active_data == res)
            merged_rects = []
            if res['rects']:
                curr_rect = fitz.Rect(res['rects'][0])
                for r in res['rects'][1:]:
                    if curr_rect.y0 < r.y1 and curr_rect.y1 > r.y0:
                        curr_rect |= r 
                    else:
                        merged_rects.append(curr_rect)
                        curr_rect = fitz.Rect(r)
                merged_rects.append(curr_rect)

            quads = [r.quad for r in merged_rects]
            if quads:
                annot = page.add_highlight_annot(quads)
                if annot:
                    annot.set_colors(stroke=(1.0, 0.4, 0.0) if is_active else (1.0, 1.0, 0.0))
                    annot.update()
                    temp_annots.append(annot)
                    
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom_factor, self.zoom_factor))
        for a in temp_annots: page.delete_annot(a)
        fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
        return QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy())

    def prev_page(self):
        if self.current_page > 0: self.current_page -= 1; self.setup_pages_layout()

    def next_page(self):
        if self.current_page < len(self.doc)-1: self.current_page += 1; self.setup_pages_layout()
    
    def prev_page(self):
        if self.current_page > 0: 
            self.current_page -= 1
            self.setup_pages_layout()

    def next_page(self):
        if self.current_page < len(self.doc)-1: 
            self.current_page += 1
            self.setup_pages_layout()

    # --- 【新增】：恢复页面跳转的逻辑代码 ---
    def jump_to_page_from_input(self):
        text = self.page_jump_input.text()
        if not text.isdigit(): 
            return
        page_num = int(text) - 1 # 用户输入的是 1 开始的页码，程序底层是 0 开始的索引
        
        if self.doc and 0 <= page_num < len(self.doc):
            self.current_page = page_num
            self.setup_pages_layout()
            self.page_jump_input.clear() # 跳转成功后清空输入框
            self.page_jump_input.clearFocus()

    # --- 【新增与修改】：精细缩放控制逻辑 ---
    def zoom_out_step(self):
        """点击减号按钮：缩小 5%"""
        current_val = self.zoom_slider.value()
        # 确保不会低于最小范围 50
        self.zoom_slider.setValue(max(50, current_val - 5))

    def zoom_in_step(self):
        """点击加号按钮：放大 5%"""
        current_val = self.zoom_slider.value()
        # 确保不会超过最大范围 400
        self.zoom_slider.setValue(min(400, current_val + 5))

    def handle_zoom(self, val):
        """滑块或按钮改变数值时触发的统一处理逻辑"""
        # 因为滑块精度改成了 50~400，所以这里要除以 100.0 还原为真正的缩放倍率
        self.zoom_factor = val / 100.0
        self.setup_pages_layout()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    # --- 【新增】：全局禁用所有对话框标题栏的“？”帮助按钮 ---
    app.setAttribute(Qt.AA_DisableWindowContextHelpButton)
    # ----------------------------------------------------------
    ex = PDFReader()
    ex.show()
    sys.exit(app.exec_())