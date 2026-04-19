import sys
import re
import os
import fitz  # PyMuPDF
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QListWidget, QLabel, 
                             QScrollArea, QFileDialog, QMessageBox, QSplitter, 
                             QSlider, QComboBox, QInputDialog, QLineEdit, QAction, 
                             QTreeWidgetItem, QTreeWidget) #

from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor
from PyQt5.QtCore import Qt, QSettings,QTimer

# ==========================================
# 自定义标签类：实现字符级精准文本选择 (极限懒加载版)
# ==========================================
class SmartTextLabel(QLabel):
    def __init__(self, doc, page_num, zoom_factor, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.page_num = page_num
        self.zoom_factor = zoom_factor
        self.chars = []
        self.is_chars_loaded = False # 【新增】：标记是否已经提取过坐标
        
        self.start_idx = None
        self.current_idx = None

    def load_chars_if_needed(self):
        """【新增】：只有在需要选中文本时，才去解析这页的底层字符"""
        if self.is_chars_loaded: return
        page_dict = self.doc[self.page_num].get_text("rawdict")
        for block in page_dict.get("blocks", []):
            if block.get("type") == 0:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        for c in span.get("chars", []):
                            self.chars.append({'rect': fitz.Rect(c['bbox']), 'c': c['c']})
        self.is_chars_loaded = True

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
            self.load_chars_if_needed() # 【核心修复】：鼠标按下的瞬间才加载坐标！
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

        # 【新增】：模式切换状态
        self.is_continuous_mode = False
        
        self.settings = QSettings("pdfsearch-config.ini", QSettings.IniFormat)
        # 【新增】：用于缩放防抖的定时器
        self.zoom_timer = QTimer()
        self.zoom_timer.setSingleShot(True) # 设置为只触发一次
        self.zoom_timer.timeout.connect(self.setup_pages_layout)

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
        
        # 1. 文件菜单 (保留你之前的)
        file_menu = menubar.addMenu('文件')
        open_act = QAction('打开', self)
        open_act.setShortcut('Ctrl+O')
        open_act.triggered.connect(self.open_file)
        
        save_act = QAction('保存', self)
        save_act.setShortcut('Ctrl+S')
        save_act.triggered.connect(self.direct_save)
        
        save_as_act = QAction('另存为', self)
        save_as_act.setShortcut('Ctrl+Shift+S')
        save_as_act.triggered.connect(self.save_as_file)
        
        close_act = QAction('关闭', self)
        close_act.setShortcut('Ctrl+W')
        close_act.triggered.connect(self.close_file)
        
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        file_menu.addAction(save_act)
        file_menu.addAction(save_as_act)
        file_menu.addSeparator()
        file_menu.addAction(close_act)

        # 2. 【新增】：编辑菜单（专治各种快捷键失效）
        edit_menu = menubar.addMenu('编辑')
        find_act = QAction('搜索正文', self)
        find_act.setShortcut('Ctrl+F') # 绑定在这，绝对有效
        find_act.triggered.connect(self.focus_search_box)
        edit_menu.addAction(find_act)

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
        # --- 原本是 self.bookmark_list = QListWidget()，现在替换为： ---
        self.bookmark_tree = QTreeWidget()
        self.bookmark_tree.setHeaderLabels(["名称", "页码"]) # 设置表头
        self.bookmark_tree.setColumnWidth(0, 140)       # 给名字留宽一点
        self.bookmark_tree.setRootIsDecorated(False)    # 隐藏树形控件前面的折叠箭头留白
        self.bookmark_tree.setIndentation(0)
        
        self.bookmark_tree.itemClicked.connect(self.on_bookmark_clicked)
        # 绑定编辑完成后的信号
        self.bookmark_tree.itemChanged.connect(self.on_bookmark_edited)
        
        bookmark_layout.addWidget(self.bookmark_tree)

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

        # 【新增】：模式切换按钮
        mid_toolbar.addSpacing(20)
        self.btn_toggle_mode = QPushButton('连续滚动')
        self.btn_toggle_mode.clicked.connect(self.toggle_view_mode)
        mid_toolbar.addWidget(self.btn_toggle_mode)
        
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

        # 【新增】：一旦滚动条被拖动，立刻触发可视范围检查
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.on_scroll_changed)
        
        center_layout.addWidget(self.scroll_area)



        # --- 3. 右侧面板：正则搜索 ---
        self.search_panel = QWidget()
        search_layout = QVBoxLayout(self.search_panel)
        search_layout.addWidget(QLabel("<b>正则搜索</b>"))
        
        self.search_combo = QComboBox()
        self.search_combo.activated.connect(self.on_presets_selected)
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
        # 【新增下面这一行】：监听最原始的鼠标点击事件
        self.results_list.itemClicked.connect(self.on_result_clicked)
        search_layout.addWidget(self.results_list)

        self.main_splitter.addWidget(self.bookmark_panel)
        self.main_splitter.addWidget(self.center_panel)
        self.main_splitter.addWidget(self.search_panel)
        self.main_splitter.setSizes([200, 800, 300])


    def focus_search_box(self):
            """当按下 Ctrl+F 时，让正则输入框获取焦点并全选现有文字"""
            # 强制抢夺焦点，无视其他控件的阻挡
            self.current_regex_input.setFocus(Qt.ShortcutFocusReason)
            self.current_regex_input.selectAll()

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

    def render_bookmark_list(self, filter_text=""):
        self.bookmark_tree.blockSignals(True) # 暂时屏蔽信号，防止在生成列表时触发编辑事件
        self.bookmark_tree.clear()
        
        for i, bk in enumerate(self.all_bookmarks):
            if filter_text.lower() in bk['name'].lower():
                # 分两列放入：名称 和 页码
                item = QTreeWidgetItem([bk['name'], f"P{bk['page']+1}"])
                item.setData(0, Qt.UserRole, i)
                
                # 【核心】：赋予第一列（名称）可双击编辑的权限！
                item.setFlags(item.flags() | Qt.ItemIsEditable) 
                
                self.bookmark_tree.addTopLevelItem(item)
                
        self.bookmark_tree.blockSignals(False)

    def add_bookmark(self):
        """点击添加，直接新增一行并立刻进入编辑状态"""
        if not self.doc: return
        
        # 默认给个占位名字，不弹窗！
        new_name = f"新书签 {len(self.all_bookmarks)+1}"
        self.all_bookmarks.append({"name": new_name, "page": self.current_page})
        self.sync_bookmarks_to_doc()
        self.render_bookmark_list()
        
        # 找到刚刚新增的那一行（最后一行），并用代码模拟双击，强制它立刻进入输入状态！
        last_item = self.bookmark_tree.topLevelItem(self.bookmark_tree.topLevelItemCount() - 1)
        if last_item:
            self.bookmark_tree.setCurrentItem(last_item)
            self.bookmark_tree.editItem(last_item, 0)

    def on_bookmark_edited(self, item, column):
        """当用户双击修改完文字，按下回车或点击空白处时触发"""
        if column == 0: 
            idx = item.data(0, Qt.UserRole)
            new_name = item.text(0).strip()
            
            # 如果用户把名字删光了，强行给个名字防止变空
            if not new_name: 
                new_name = "未命名"
                item.setText(0, new_name)
                
            # 只有名字真变了才去保存
            if self.all_bookmarks[idx]['name'] != new_name:
                self.all_bookmarks[idx]['name'] = new_name
                self.sync_bookmarks_to_doc()

    def delete_bookmark(self):
        if not self.doc: return
        item = self.bookmark_tree.currentItem()
        if item:
            idx = item.data(0, Qt.UserRole)
            del self.all_bookmarks[idx]
            self.sync_bookmarks_to_doc()
            self.render_bookmark_list()

    def on_bookmark_clicked(self, item, column):
            """单击条目立刻跳转"""
            idx = item.data(0, Qt.UserRole)
            # 修改为调用 go_to_page
            self.go_to_page(self.all_bookmarks[idx]['page'])


    def filter_bookmarks(self, text):
        self.render_bookmark_list(text)

    # --- 文件操作 ---
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", "", "PDF (*.pdf)")

        if path:
            # 清空当前文件状态
            self.close_file()
            self.doc = fitz.open(path)
            self.current_page = 0
            
            # 【重要】：打开文件后立刻从 PDF 读取它原生的书签
            self.load_bookmarks() 
            
            self.setup_pages_layout()
            self.setWindowTitle(f"高级正则 PDF 阅读器 - {os.path.basename(path)}")

    def close_file(self):
        """关闭当前文档并彻底清空所有相关的 UI 和底层数据"""
        if self.doc:
            self.doc.close()
            self.doc = None
            
        # 1. 重置窗口标题和页码信息
        self.setWindowTitle('高级正则 PDF 阅读器')
        self.current_page = 0
        self.page_label.setText('第 0 / 0 页')
        if hasattr(self, 'page_jump_input'):
            self.page_jump_input.clear()

        # 2. 彻底清空书签相关内容
        self.all_bookmarks = []            
        self.bookmark_tree.clear()         
        self.bookmark_search.clear()       

        # 3. 【核心修复】：彻底清空正则搜索相关的 UI 与 底层数据
        self.results_list.clear()
        self.result_count_label.setText('共 0 条结果')
        self.search_results_data = []      # <--- 就是漏了这一行！必须清空旧坐标！
        self.active_data = None
        self.current_regex_input.clear()   # 顺手把搜索框里的字也清掉，保持界面整洁
        
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
        """加载预设，并自动恢复上一次选中的项"""
        self.search_combo.blockSignals(True) # 暂时屏蔽信号，防止清空时触发误操作
        self.search_combo.clear()
        
        presets = self.settings.value("regex_presets", [])
        for p in presets:
            self.search_combo.addItem(p['name'])
            
        # 【核心新增】：读取上一次保存的索引，默认值为 0
        last_idx = self.settings.value("last_preset_index", 0, type=int)
        
        # 确保索引合法（防止预设被删后越界）
        if 0 <= last_idx < self.search_combo.count():
            self.search_combo.setCurrentIndex(last_idx)
            # 同步把正则文本填入搜索框
            self.current_regex_input.setText(presets[last_idx]['regex'])
        elif self.search_combo.count() > 0:
            self.search_combo.setCurrentIndex(0)
            self.current_regex_input.setText(presets[0]['regex'])
            
        self.search_combo.blockSignals(False)

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

    def on_presets_selected(self, idx):
        """下拉框切换时触发：更新输入框，并记住当前选择"""
        if idx >= 0:
            presets = self.settings.value("regex_presets", [])
            if idx < len(presets):
                # 1. 更新文本框里的正则表达式
                self.current_regex_input.setText(presets[idx]['regex'])
                
                # 2. 【核心】：将当前索引永久写入本地配置文件
                self.settings.setValue("last_preset_index", idx)

    

    def perform_search(self):
        if not self.doc: return
        pattern = self.current_regex_input.text()
        
        self.results_list.clear()
        self.search_results_data.clear()
        self.active_data = None
        
        if not pattern: 
            self.result_count_label.setText("共 0 条结果")
            self.setup_pages_layout()
            return
            
        try: regex = re.compile(pattern)
        except Exception as e:
            QMessageBox.warning(self, '语法错误', str(e))
            return
        
        res_id_counter = 0 # 【终极修复 1】：给每个结果分配全局唯一的整数 ID
        
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
                    if (ord(prev_ch) > 255) and (ord(next_ch) > 255): continue
                    else: clean_chars.append((' ', rect))
                else: clean_chars.append((ch, rect))
                    
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
                    'id': res_id_counter, # 存入唯一 ID
                    'page': page_num, 
                    'str': m_str, 
                    'rects': match_rects 
                })
                res_id_counter += 1 # ID自增

        self.result_count_label.setText(f"共 {len(self.search_results_data)} 条结果")

        # 【终极修复：绝对可靠的初始定位逻辑】
        if self.search_results_data:
            # 1. 直接把第一条结果设为激活项，并更新当前页码
            self.active_data = self.search_results_data[0]
            self.current_page = self.active_data['page']
            
            # 2. 带着第一条的“橙色高亮”记忆，去执行排版和自动跳转
            self.setup_pages_layout()
            
            # 3. 默默把右侧列表的第一行高亮选中，但堵住它的嘴（屏蔽信号）
            # 防止它再次触发 on_result_change 导致页面被毫无意义地重绘两次
            self.results_list.blockSignals(True)
            self.results_list.setCurrentRow(0)
            self.results_list.blockSignals(False)
        else:
            # 如果没搜到东西，就按原样排版并清空高亮
            self.setup_pages_layout()

    def on_result_change(self, cur, prev):
        if cur:
            # 记录旧页码
            old_page = self.active_data['page'] if self.active_data else None
            
            # 更新当前激活的数据
            res = self.search_results_data[self.results_list.row(cur)]
            self.active_data = res 
            new_page = res['page']
            
            # 【修复 1：顺序至关重要】必须先执行跳转！
            # 这样在单页模式下，目标页才会被创建出来，后续的刷新才能精准命中。
            self.go_to_page(new_page)
            
            # 【修复 2：局部刷新】褪去旧的，点亮新的
            if old_page is not None and old_page != new_page:
                self.refresh_page_render(old_page)
            self.refresh_page_render(new_page)

    def on_result_clicked(self, item):
        """专门处理鼠标重复点击同一条结果时的强行跳转"""
        # 取出被点击的那一行的数据
        res = self.search_results_data[self.results_list.row(item)]
        
        # 不管三七二十一，强行让画面切回这一页！
        # （因为如果是同一条结果，深橙色高亮本身就还在它身上，不需要重新画，切过去就行）
        self.go_to_page(res['page'])

    def refresh_page_render(self, page_num):
        """极速局部刷新，兼顾剥离占位空壳的功能"""
        for p, lbl in self.page_labels:
            if p == page_num:
                # 不管它是不是空壳，强制变回真图
                lbl.setText("")
                lbl.setStyleSheet("")
                lbl.setPixmap(self.get_page_pixmap(p))
                self.rendered_pages.add(p)
                lbl.repaint() 
                break

    

    # ==========================================
    # --- 渲染与导航 (双模式兼容版) ---
    # ==========================================
    def toggle_view_mode(self):
        if not self.doc: return
        self.is_continuous_mode = not self.is_continuous_mode
        self.btn_toggle_mode.setText('单页显示' if self.is_continuous_mode else '连续滚动')
        self.setup_pages_layout()


    
    # ==========================================
    # --- 懒加载 (按需渲染) 核心逻辑 ---
    # ==========================================
    def on_scroll_changed(self, value):
        if self.is_continuous_mode:
            self.render_visible_pages()
            self.update_page_label_from_scroll()

    def render_visible_pages(self):
        """核心魔法：计算当前屏幕可见的页面，只渲染它们"""
        if not self.is_continuous_mode or not self.doc: return
        
        # 获取当前可视区的顶部和底部 Y 坐标
        scroll_y = self.scroll_area.verticalScrollBar().value()
        viewport_height = self.scroll_area.viewport().height()
        visible_start = scroll_y
        visible_end = scroll_y + viewport_height
        
        # 上下多加载一页高度作为缓冲，防止滑动时有白底闪烁
        buffer = viewport_height 
        
        for p, lbl in self.page_labels:
            lbl_top = lbl.y()
            lbl_bottom = lbl_top + lbl.height()
            
            # 判断这个标签是否进入了视野（或缓冲）范围
            if lbl_bottom >= (visible_start - buffer) and lbl_top <= (visible_end + buffer):
                if p not in self.rendered_pages:
                    # 如果进入视野且没被渲染过，立刻撕掉空壳，画上真图！
                    lbl.setText("") 
                    lbl.setStyleSheet("") # 清除占位时的边框
                    lbl.setPixmap(self.get_page_pixmap(p))
                    self.rendered_pages.add(p)

    def update_page_label_from_scroll(self):
        """滚动时自动更新顶部工具栏的当前页码"""
        scroll_y = self.scroll_area.verticalScrollBar().value()
        for p, lbl in self.page_labels:
            # 只要某页的一半已经越过了屏幕顶端，就认为正在看这一页
            if lbl.y() + lbl.height() / 2 > scroll_y:
                if self.current_page != p:
                    self.current_page = p
                    self.page_label.setText(f"第 {self.current_page+1} / {len(self.doc)} 页")
                break

    def go_to_page(self, page_num):
        """统一的页面跳转核心逻辑"""
        if not self.doc or not (0 <= page_num < len(self.doc)): return
        self.current_page = page_num
        
        if self.is_continuous_mode:
            # 连续模式下不需要重新渲染，直接滚动到对应标签位置
            for p, lbl in self.page_labels:
                if p == self.current_page:
                    self.scroll_area.verticalScrollBar().setValue(lbl.y())
                    self.page_label.setText(f"第 {self.current_page+1} / {len(self.doc)} 页")
                    break
        else:
            # 单页模式下直接重新渲染该页
            self.setup_pages_layout()

    def setup_pages_layout(self):
        if not self.doc: return
        
        while self.pdf_layout.count():
            w = self.pdf_layout.takeAt(0).widget()
            if w: w.deleteLater()
            
        self.page_labels.clear()
        self.rendered_pages.clear() # 【重要】：重新布局时清空已渲染记录
        
        if self.is_continuous_mode:
            # --- 连续模式：极速生成占位骨架 ---
            for p in range(len(self.doc)):
                lbl = SmartTextLabel(self.doc, p, self.zoom_factor)
                lbl.setAlignment(Qt.AlignCenter)
                
                # 提前计算这一页在当前缩放比下的物理尺寸
                page_rect = self.doc[p].rect
                width = int(page_rect.width * self.zoom_factor)
                height = int(page_rect.height * self.zoom_factor)
                
                # 生成占位空壳
                lbl.setFixedSize(width, height)
                lbl.setStyleSheet("background-color: #ffffff; border: 1px solid #cccccc; color: #999999; font-size: 16px;")
                lbl.setText(f"加载中... P{p+1}")
                
                self.pdf_layout.addWidget(lbl)
                self.page_labels.append((p, lbl))
            
            self.page_label.setText(f"第 {self.current_page+1} / {len(self.doc)} 页")
            
            # 等待骨架生成后，直接跳转到当前页（这会触发滚动条变化，进而激活懒加载）
            QTimer.singleShot(50, lambda: self.go_to_page(self.current_page))
        else:
            # --- 单页模式保持不变 ---
            lbl = SmartTextLabel(self.doc, self.current_page, self.zoom_factor)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setPixmap(self.get_page_pixmap(self.current_page))
            self.pdf_layout.addWidget(lbl)
            self.page_labels.append((self.current_page, lbl))
            self.page_label.setText(f"第 {self.current_page+1} / {len(self.doc)} 页")

    def prev_page(self):
        self.go_to_page(self.current_page - 1)

    def next_page(self):
        self.go_to_page(self.current_page + 1)

    def jump_to_page_from_input(self):
        text = self.page_jump_input.text()
        if text.isdigit():
            self.go_to_page(int(text) - 1)
            self.page_jump_input.clear()
            self.page_jump_input.clearFocus()

    def handle_zoom(self, val):
        self.zoom_factor = val / 100.0
        
        # 以前是直接调用 self.setup_pages_layout()，太暴力了
        # 现在改成：重置并启动定时器，等待 500 毫秒
        self.zoom_timer.start(500)

    def zoom_out_step(self):
        self.zoom_slider.setValue(max(50, self.zoom_slider.value() - 10))

    def zoom_in_step(self):
        self.zoom_slider.setValue(min(400, self.zoom_slider.value() + 10))

    def get_page_pixmap(self, p_num):
        page = self.doc[p_num]
        temp_annots = []
        page_results = [res for res in self.search_results_data if res['page'] == p_num]
        
        def draw_highlight(res_data, color):
            merged_rects = []
            if res_data.get('rects'):
                curr_rect = fitz.Rect(res_data['rects'][0])
                for r in res_data['rects'][1:]:
                    if curr_rect.y0 < r.y1 and curr_rect.y1 > r.y0:
                        curr_rect |= fitz.Rect(r) 
                    else:
                        merged_rects.append(curr_rect)
                        curr_rect = fitz.Rect(r)
                merged_rects.append(curr_rect)

            quads = [r.quad for r in merged_rects]
            if quads:
                annot = page.add_highlight_annot(quads)
                if annot:
                    annot.set_colors(stroke=color)
                    annot.update()
                    temp_annots.append(annot)

        # 【终极修复 2】：提取当前激活项的唯一 ID
        active_id = self.active_data['id'] if self.active_data else -1

        for res in page_results:
            if res['id'] == active_id: 
                continue # 是激活项就跳过
            draw_highlight(res, (1.0, 1.0, 0.0))

        # 只针对 ID 完全吻合的目标橙色高亮
        if self.active_data and self.active_data['page'] == p_num:
            draw_highlight(self.active_data, (1.0, 0.4, 0.0))
                    
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom_factor, self.zoom_factor))
        for a in temp_annots: page.delete_annot(a)
        fmt = QImage.Format_RGBA8888 if pix.alpha else QImage.Format_RGB888
        return QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy())


if __name__ == '__main__':
    app = QApplication(sys.argv)
    # --- 【新增】：全局禁用所有对话框标题栏的“？”帮助按钮 ---
    app.setAttribute(Qt.AA_DisableWindowContextHelpButton)
    # ----------------------------------------------------------
    ex = PDFReader()
    ex.show()
    sys.exit(app.exec_())