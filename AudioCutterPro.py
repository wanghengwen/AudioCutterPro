import wx
import wx.lib.newevent
import os
import threading
import time
import numpy as np
import matplotlib
from matplotlib.figure import Figure
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas
from pydub import AudioSegment
import pyaudio

# === 新增：引入 mutagen 用于完美拷贝标签 ===
from mutagen import File
from mutagen.id3 import ID3, ID3NoHeaderError

# 设置 matplotlib 不要在独立窗口显示
matplotlib.use('WXAgg')

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont

# --- Matplotlib 中文字体配置 ---
try:
    font_path = findfont(FontProperties(family=['WenQuanYi Zen Hei', 'Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC']))
    chinese_font = FontProperties(fname=font_path)
    plt.rcParams['font.family'] = chinese_font.get_name()
    plt.rcParams['axes.unicode_minus'] = False 
except Exception:
    plt.rcParams['font.family'] = 'DejaVu Sans'
    plt.rcParams['axes.unicode_minus'] = False

# 定义自定义事件
UpdatePlotEvent, EVT_UPDATE_PLOT = wx.lib.newevent.NewEvent()

class AudioEditorFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="AudioCutter Pro", size=(1300, 850))
        self.SetBackgroundColour(wx.Colour(245, 247, 250))
        
        self.CreateStatusBar()
        self.SetStatusText("就绪")

        # --- 数据状态 ---
        self.audio_segment = None 
        self.file_path = None
        self.sample_rate = 44100
        self.duration_sec = 0
        
        # 播放相关
        self.p = pyaudio.PyAudio()
        self.is_playing = False         
        self.is_playing_main = False    
        self.stop_play_event = threading.Event() 
        
        self.playback_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_playback_timer, self.playback_timer)
        self.play_start_time_system = 0 
        self.play_start_cursor = 0      

        self.start_mark = 0.0
        self.end_mark = 0.0
        self.current_cursor_time = 0.0 
        self.cursor_line = None
        
        self.last_head_trim = 0.0
        self.last_tail_trim = 0.0
        self.has_processed_once = False

        self.is_panning = False
        self.press_x_pixel = 0
        self.initial_xlim = (0, 1)
        
        self.MARKER_DONE = "✅ "

        self.init_ui()
        self.Layout()
        self.Centre()

    def init_ui(self):
        self.font_normal = wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL, False, "Microsoft YaHei")
        self.font_bold = wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD, False, "Microsoft YaHei")
        
        def create_label(parent, text):
            lbl = wx.StaticText(parent, label=text)
            lbl.SetFont(self.font_normal)
            return lbl

        main_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # === 左侧 ===
        left_panel = wx.Panel(self, size=(260, -1))
        left_panel.SetBackgroundColour(wx.Colour(255, 255, 255))
        left_sizer = wx.BoxSizer(wx.VERTICAL)

        title_lbl = wx.StaticText(left_panel, label="AudioCutter Pro")
        title_font = wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD, False, "Microsoft YaHei")
        title_lbl.SetFont(title_font)
        title_lbl.SetForegroundColour(wx.Colour(24, 144, 255)) 
        left_sizer.Add(title_lbl, 0, wx.ALL, 15)

        self.btn_dir = wx.Button(left_panel, label="选择音频目录", size=(-1, 40))
        self.btn_dir.SetBackgroundColour(wx.Colour(24, 144, 255))
        self.btn_dir.SetForegroundColour(wx.WHITE)
        self.btn_dir.SetFont(self.font_normal)
        self.btn_dir.Bind(wx.EVT_BUTTON, self.on_choose_dir)
        left_sizer.Add(self.btn_dir, 0, wx.EXPAND | wx.ALL, 15)

        self.file_list = wx.ListBox(left_panel, style=wx.LB_SINGLE)
        self.file_list.SetFont(self.font_normal)
        self.file_list.Bind(wx.EVT_LISTBOX, self.on_file_selected)
        left_sizer.Add(self.file_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 15)

        left_panel.SetSizer(left_sizer)
        main_sizer.Add(left_panel, 0, wx.EXPAND)

        line = wx.StaticLine(self, style=wx.LI_VERTICAL)
        main_sizer.Add(line, 0, wx.EXPAND)

        # === 右侧 ===
        right_panel = wx.Panel(self)
        right_sizer = wx.BoxSizer(wx.VERTICAL)

        self.wave_panel = wx.Panel(right_panel)
        self.wave_panel.SetBackgroundColour(wx.WHITE)
        wave_sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.figure.patch.set_facecolor('#FFFFFF')
        self.figure.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.05)
        
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#FAFAFA')
        self.canvas = FigureCanvas(self.wave_panel, -1, self.figure)
        
        self.canvas.mpl_connect('button_press_event', self.on_wave_press)
        self.canvas.mpl_connect('button_release_event', self.on_wave_release)
        self.canvas.mpl_connect('motion_notify_event', self.on_wave_motion)
        self.canvas.mpl_connect('scroll_event', self.on_wave_scroll)
        
        wave_sizer.Add(self.canvas, 1, wx.EXPAND | wx.ALL, 5) 
        self.wave_panel.SetSizer(wave_sizer)
        right_sizer.Add(self.wave_panel, 1, wx.EXPAND | wx.ALL, 5) 

        bottom_area = wx.Panel(right_panel)
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)

        cut_box = wx.StaticBox(bottom_area, label="裁剪范围 & 预览")
        cut_box.SetFont(self.font_normal)
        cut_sizer = wx.StaticBoxSizer(cut_box, wx.VERTICAL)
        
        time_row = wx.BoxSizer(wx.HORIZONTAL)
        
        start_grp = wx.BoxSizer(wx.VERTICAL)
        self.btn_set_start = wx.Button(bottom_area, label="设为起点", size=(100, 35))
        self.btn_set_start.SetBackgroundColour(wx.Colour(64, 169, 255))
        self.btn_set_start.SetForegroundColour(wx.WHITE)
        self.btn_set_start.SetFont(self.font_normal)
        self.btn_set_start.Bind(wx.EVT_BUTTON, self.on_set_start)
        self.txt_start_time = wx.TextCtrl(bottom_area, value="0.000", style=wx.TE_READONLY|wx.TE_CENTER)
        start_grp.Add(create_label(bottom_area, "开始时间"), 0, wx.BOTTOM, 5)
        start_grp.Add(self.btn_set_start, 0, wx.BOTTOM, 5)
        start_grp.Add(self.txt_start_time, 0, wx.EXPAND)
        
        end_grp = wx.BoxSizer(wx.VERTICAL)
        self.btn_set_end = wx.Button(bottom_area, label="设为终点", size=(100, 35))
        self.btn_set_end.SetBackgroundColour(wx.Colour(255, 77, 79))
        self.btn_set_end.SetForegroundColour(wx.WHITE)
        self.btn_set_end.SetFont(self.font_normal)
        self.btn_set_end.Bind(wx.EVT_BUTTON, self.on_set_end)
        self.txt_end_time = wx.TextCtrl(bottom_area, value="0.000", style=wx.TE_READONLY|wx.TE_CENTER)
        end_grp.Add(create_label(bottom_area, "结束时间"), 0, wx.BOTTOM, 5)
        end_grp.Add(self.btn_set_end, 0, wx.BOTTOM, 5)
        end_grp.Add(self.txt_end_time, 0, wx.EXPAND)

        time_row.Add(start_grp, 1, wx.ALL, 10)
        time_row.Add(end_grp, 1, wx.ALL, 10)
        cut_sizer.Add(time_row, 0, wx.EXPAND)

        play_row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_play = wx.Button(bottom_area, label="▶ 播放", size=(100, 35))
        self.btn_play.SetBackgroundColour(wx.Colour(82, 196, 26))
        self.btn_play.SetForegroundColour(wx.WHITE)
        self.btn_play.SetFont(self.font_normal)
        self.btn_play.Bind(wx.EVT_BUTTON, self.on_btn_play)
        
        self.btn_pause = wx.Button(bottom_area, label="⏸ 暂停", size=(100, 35))
        self.btn_pause.SetBackgroundColour(wx.Colour(250, 173, 20))
        self.btn_pause.SetForegroundColour(wx.WHITE)
        self.btn_pause.SetFont(self.font_normal)
        self.btn_pause.Bind(wx.EVT_BUTTON, self.on_btn_pause)

        play_row.Add(self.btn_play, 0, wx.RIGHT, 20)
        play_row.Add(self.btn_pause, 0)
        
        cut_sizer.Add(play_row, 0, wx.ALIGN_CENTER | wx.TOP | wx.BOTTOM, 10)
        
        self.lbl_duration = wx.StaticText(bottom_area, label="选定时长: 00:00.00")
        self.lbl_duration.SetFont(self.font_bold)
        self.lbl_duration.SetForegroundColour(wx.Colour(24, 144, 255))
        cut_sizer.Add(self.lbl_duration, 0, wx.ALIGN_CENTER | wx.TOP, 5)

        export_box = wx.StaticBox(bottom_area, label="导出参数")
        export_box.SetFont(self.font_normal)
        export_sizer = wx.StaticBoxSizer(export_box, wx.VERTICAL)

        fade_row = wx.BoxSizer(wx.HORIZONTAL)
        fade_row.Add(create_label(bottom_area, "淡入(s):"), 0, wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, 5)
        self.spin_fade_in = wx.SpinCtrlDouble(bottom_area, value="2.0", min=0, max=60, inc=0.5, size=(60,-1))
        fade_row.Add(self.spin_fade_in, 1, wx.RIGHT|wx.EXPAND, 10)
        fade_row.Add(create_label(bottom_area, "淡出(s):"), 0, wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, 5)
        self.spin_fade_out = wx.SpinCtrlDouble(bottom_area, value="2.0", min=0, max=60, inc=0.5, size=(60,-1))
        fade_row.Add(self.spin_fade_out, 1, wx.EXPAND) 
        export_sizer.Add(fade_row, 0, wx.EXPAND | wx.BOTTOM, 10)

        norm_row = wx.BoxSizer(wx.HORIZONTAL)
        self.chk_norm = wx.CheckBox(bottom_area, label="音量均衡 (dB):")
        self.chk_norm.SetFont(self.font_normal)
        self.chk_norm.SetValue(True)
        self.txt_db = wx.TextCtrl(bottom_area, value="-14", size=(50, -1))
        norm_row.Add(self.chk_norm, 0, wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, 5)
        norm_row.Add(self.txt_db, 0, wx.ALIGN_CENTER_VERTICAL)
        export_sizer.Add(norm_row, 0, wx.EXPAND | wx.BOTTOM, 10)

        format_grid = wx.FlexGridSizer(2, 4, 8, 10) 
        format_grid.AddGrowableCol(1, 1)
        format_grid.AddGrowableCol(3, 1)
        
        format_grid.Add(create_label(bottom_area, "格式:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.cb_format = wx.ComboBox(bottom_area, choices=["mp3", "wav"], style=wx.CB_READONLY, value="mp3")
        format_grid.Add(self.cb_format, 1, wx.EXPAND)

        format_grid.Add(create_label(bottom_area, "码率:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.cb_bitrate = wx.ComboBox(bottom_area, choices=["64k", "96k", "128k", "192k", "320k"], style=wx.CB_READONLY, value="128k")
        format_grid.Add(self.cb_bitrate, 1, wx.EXPAND)

        format_grid.Add(create_label(bottom_area, "通道:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.cb_channels = wx.ComboBox(bottom_area, choices=["立体声", "单声道"], style=wx.CB_READONLY, value="立体声")
        format_grid.Add(self.cb_channels, 1, wx.EXPAND)
        
        format_grid.Add(create_label(bottom_area, "采样:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.cb_sample_rate = wx.ComboBox(bottom_area, choices=["16000", "22050", "44100", "48000"], style=wx.CB_READONLY, value="16000")
        format_grid.Add(self.cb_sample_rate, 1, wx.EXPAND)
        
        export_sizer.Add(format_grid, 0, wx.EXPAND | wx.BOTTOM, 10)

        path_row = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_save_path = wx.TextCtrl(bottom_area, value="New", style=wx.TE_READONLY)
        self.btn_change_path = wx.Button(bottom_area, label="更改", size=(50, -1))
        self.btn_change_path.Bind(wx.EVT_BUTTON, self.on_change_save_path)
        
        path_row.Add(create_label(bottom_area, "保存:"), 0, wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, 5)
        path_row.Add(self.txt_save_path, 1, wx.RIGHT, 5)
        path_row.Add(self.btn_change_path, 0)
        export_sizer.Add(path_row, 0, wx.EXPAND | wx.BOTTOM, 15)

        self.btn_process = wx.Button(bottom_area, label="开始处理并保存", size=(-1, 45))
        self.btn_process.SetBackgroundColour(wx.Colour(32, 168, 133))
        self.btn_process.SetForegroundColour(wx.WHITE)
        self.btn_process.SetFont(self.font_bold)
        self.btn_process.Bind(wx.EVT_BUTTON, self.on_process)
        export_sizer.Add(self.btn_process, 0, wx.EXPAND)

        bottom_sizer.Add(cut_sizer, 3, wx.EXPAND | wx.ALL, 10)
        bottom_sizer.Add(export_sizer, 2, wx.EXPAND | wx.ALL, 10)
        bottom_area.SetSizer(bottom_sizer)

        right_sizer.Add(bottom_area, 0, wx.EXPAND)
        right_panel.SetSizer(right_sizer)
        
        main_sizer.Add(right_panel, 1, wx.EXPAND)
        self.SetSizer(main_sizer)

    # --- 逻辑部分 ---

    def on_choose_dir(self, event):
        dlg = wx.DirDialog(self, "选择音频目录", style=wx.DD_DEFAULT_STYLE)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            file_count = self.load_file_list(path)
            self.txt_save_path.SetValue(os.path.join(path, "New"))
            
            if file_count == 0:
                 wx.MessageBox("该目录下没有找到音频文件 (.mp3, .wav, .ogg, .flac, .m4a)", "提示", wx.ICON_WARNING)
            else:
                self.file_list.SetSelection(0) 
                self.on_file_selected(None) 
        dlg.Destroy()

    def on_change_save_path(self, event):
        dlg = wx.DirDialog(self, "选择保存目录", style=wx.DD_DEFAULT_STYLE)
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_save_path.SetValue(dlg.GetPath())
        dlg.Destroy()

    def load_file_list(self, folder_path):
        self.file_list.Clear()
        self.current_folder = folder_path
        extensions = ('.mp3', '.wav', '.ogg', '.flac', '.m4a')
        files = [f for f in os.listdir(folder_path) if f.lower().endswith(extensions)]
        for f in files:
            self.file_list.Append(f)
        return len(files)

    def on_file_selected(self, event):
        self.stop_playback_now()
        
        self.audio_segment = None
        self.plot_data = None
        self.ax.clear()
        self.canvas.draw()
        self.SetStatusText("正在加载...")

        selection = self.file_list.GetStringSelection()
        if not selection: return
        
        real_filename = selection
        if selection.startswith(self.MARKER_DONE):
            real_filename = selection[len(self.MARKER_DONE):]
            
        full_path = os.path.join(self.current_folder, real_filename)
        self.file_path = full_path
        
        threading.Thread(target=self.load_audio_thread, args=(full_path,)).start()

    def load_audio_thread(self, path):
        try:
            self.audio_segment = AudioSegment.from_file(path)
            self.duration_sec = len(self.audio_segment) / 1000.0
            
            samples = np.array(self.audio_segment.get_array_of_samples())
            if self.audio_segment.channels == 2:
                samples = samples.reshape((-1, 2))[:, 0]
            
            target_points = int(self.duration_sec * 100)
            step = max(1, len(samples) // target_points)
            self.plot_data = samples[::step]
            self.plot_time = np.linspace(0, self.duration_sec, len(self.plot_data))
            
            if self.has_processed_once:
                self.start_mark = min(self.last_head_trim, self.duration_sec)
                self.end_mark = max(self.start_mark, self.duration_sec - self.last_tail_trim)
            else:
                self.start_mark = 0.0
                self.end_mark = self.duration_sec
            
            self.current_cursor_time = 0.0
            wx.CallAfter(self.update_ui_after_load)
        except Exception as e:
            wx.CallAfter(wx.MessageBox, f"加载失败: {str(e)}", "错误", wx.ICON_ERROR)

    def update_ui_after_load(self):
        filename = os.path.basename(self.file_path)
        self.SetTitle(f"AudioCutter Pro - {filename}")
        self.SetStatusText(f"已加载: {filename}")
        self.draw_waveform(preserve_view=False)
        self.update_time_display()

    def draw_waveform(self, preserve_view=False):
        if self.plot_data is None: return
        
        current_xlim = self.ax.get_xlim() if preserve_view else None

        self.ax.clear()
        self.ax.plot(self.plot_time, self.plot_data, color='#4a90e2', linewidth=0.5)
        self.ax.set_facecolor('#FAFAFA')
        self.ax.set_yticks([])
        
        if current_xlim:
            self.ax.set_xlim(current_xlim)
        else:
            self.ax.set_xlim(0, self.duration_sec)
            
        self.ax.grid(True, axis='x', linestyle='--', alpha=0.5)
        
        self.ax.axvline(x=self.start_mark, color='#1890ff', linewidth=2)
        self.ax.axvline(x=self.end_mark, color='#ff4d4f', linewidth=2)
        self.ax.axvspan(0, self.start_mark, color='gray', alpha=0.2)
        self.ax.axvspan(self.end_mark, self.duration_sec, color='gray', alpha=0.2)
        
        self.cursor_line = self.ax.axvline(x=self.current_cursor_time, color='green', linewidth=1.5, linestyle='--')

        self.canvas.draw()

    # --- 播放控制 ---

    def stop_playback_now(self):
        self.stop_play_event.set() 
        if self.playback_timer.IsRunning():
            self.playback_timer.Stop()
        self.is_playing_main = False
        self.is_playing = False

    def on_btn_play(self, event):
        if not self.audio_segment: return
        if self.is_playing_main: return 

        self.stop_playback_now() 
        time.sleep(0.1) 
        self.stop_play_event.clear()
        
        self.is_playing_main = True
        self.SetStatusText("正在播放...")
        
        start_sec = self.current_cursor_time
        threading.Thread(target=self.play_thread_func, args=(start_sec,)).start()
        
        self.play_start_time_system = time.time()
        self.play_start_cursor = start_sec
        self.playback_timer.Start(50) 

    def on_btn_pause(self, event):
        self.stop_playback_now()
        self.SetStatusText("暂停")

    def play_thread_func(self, start_sec):
        try:
            start_ms = int(start_sec * 1000)
            chunk_data = self.audio_segment[start_ms:] 
            
            stream = self.p.open(format=self.p.get_format_from_width(chunk_data.sample_width),
                                 channels=chunk_data.channels, rate=chunk_data.frame_rate, output=True)
            
            data = chunk_data.raw_data
            chunk_size = 128 * 1024 
            idx = 0
            
            while idx < len(data) and not self.stop_play_event.is_set():
                stream.write(data[idx:idx+chunk_size])
                idx += chunk_size
                
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"Playback error: {e}")
        finally:
            if not self.stop_play_event.is_set():
                wx.CallAfter(self.on_playback_finished)

    def on_playback_finished(self):
        self.playback_timer.Stop()
        self.is_playing_main = False
        self.SetStatusText("播放结束")
        self.current_cursor_time = min(self.duration_sec, self.current_cursor_time)
        self.update_cursor_visual()

    def on_playback_timer(self, event):
        if not self.is_playing_main: return
        
        elapsed = time.time() - self.play_start_time_system
        new_time = self.play_start_cursor + elapsed
        
        if new_time > self.duration_sec:
            new_time = self.duration_sec
            
        self.current_cursor_time = new_time
        self.update_cursor_visual()
        
        curr_xlim = self.ax.get_xlim()
        if new_time > curr_xlim[1]:
            width = curr_xlim[1] - curr_xlim[0]
            new_min = new_time - width * 0.1 
            new_max = new_min + width
            if new_max > self.duration_sec: 
                new_max = self.duration_sec
                new_min = max(0, new_max - width)
            self.ax.set_xlim(new_min, new_max)
            self.canvas.draw_idle()

    def update_cursor_visual(self):
        if self.cursor_line:
            self.cursor_line.set_xdata([self.current_cursor_time])
            self.canvas.draw_idle() 

    # --- 交互 ---
    
    def on_wave_press(self, event):
        if event.inaxes != self.ax: return
        if event.button != 1: return 

        self.is_panning = True
        self.press_x_pixel = event.x 
        self.initial_xlim = self.ax.get_xlim()

    def on_wave_motion(self, event):
        if not self.is_panning or event.inaxes != self.ax: return
        
        dx_pix = event.x - self.press_x_pixel
        range_sec = self.initial_xlim[1] - self.initial_xlim[0]
        width_pix = self.ax.bbox.width
        if width_pix == 0: return
        
        scale = range_sec / width_pix
        dx_sec = dx_pix * scale
        
        new_min = self.initial_xlim[0] - dx_sec
        new_max = self.initial_xlim[1] - dx_sec
        
        if new_min < 0:
            new_min = 0
            new_max = range_sec
        if new_max > self.duration_sec:
            new_max = self.duration_sec
            new_min = max(0, new_max - range_sec)
            
        self.ax.set_xlim(new_min, new_max)
        self.canvas.draw_idle()

    def on_wave_release(self, event):
        if not self.is_panning: return
        self.is_panning = False
        
        if event.inaxes != self.ax: return
        
        if abs(event.x - self.press_x_pixel) < 5:
            self.handle_click(event.xdata)

    def handle_click(self, click_time):
        if click_time is None: return
        
        if self.is_playing_main:
            self.stop_playback_now()
            
        self.current_cursor_time = click_time
        self.update_cursor_visual()

    def on_wave_scroll(self, event):
        if event.inaxes != self.ax: return
        cur_xlim = self.ax.get_xlim()
        cur_range = cur_xlim[1] - cur_xlim[0]
        xdata = event.xdata
        
        scale_factor = 1.2
        if event.button == 'up': 
            new_range = cur_range / scale_factor
        else: 
            new_range = cur_range * scale_factor
            
        if new_range < 0.01: new_range = 0.01
        if new_range > self.duration_sec: new_range = self.duration_sec

        new_min = xdata - new_range / 2
        new_max = xdata + new_range / 2
        
        if new_min < 0:
            new_min = 0
            new_max = new_min + new_range
        if new_max > self.duration_sec:
            new_max = self.duration_sec
            new_min = new_max - new_range
            if new_min < 0: new_min = 0
        
        self.ax.set_xlim(new_min, new_max)
        self.canvas.draw()

    def on_set_start(self, event):
        if 0 <= self.current_cursor_time < self.end_mark:
            self.start_mark = self.current_cursor_time
            self.update_time_display()
            self.draw_waveform(preserve_view=True)
        else:
            wx.MessageBox("起点无效", "提示")

    def on_set_end(self, event):
        if self.start_mark < self.current_cursor_time <= self.duration_sec:
            self.end_mark = self.current_cursor_time
            self.update_time_display()
            self.draw_waveform(preserve_view=True)
        else:
            wx.MessageBox("终点无效", "提示")

    def update_time_display(self):
        self.txt_start_time.SetValue(f"{self.start_mark:.3f}")
        self.txt_end_time.SetValue(f"{self.end_mark:.3f}")
        duration = self.end_mark - self.start_mark
        mins = int(duration // 60)
        secs = duration % 60
        self.lbl_duration.SetLabel(f"选定时长: {mins:02d}:{secs:05.2f}")

    # === 新增：元数据拷贝辅助函数 ===
    def copy_metadata(self, src_path, dst_path):
        """ 使用 mutagen 将源文件的所有标签（含封面）克隆到目标文件 """
        try:
            # 1. 尝试作为 MP3 处理 (ID3)
            # 这是最健壮的方法，直接拷贝 ID3 Header
            try:
                tags = ID3(src_path)
                tags.save(dst_path)
                return # MP3 处理成功，直接返回
            except ID3NoHeaderError:
                pass # 不是 MP3 或没有 ID3 标签，继续尝试通用方法
            except Exception as e:
                print(f"ID3 copy failed: {e}")

            # 2. 通用文件处理 (FLAC, OGG, M4A 等)
            src_file = File(src_path)
            dst_file = File(dst_path)
            
            if src_file and dst_file and src_file.tags:
                if dst_file.tags is None:
                    dst_file.add_tags()
                
                # 简单键值对拷贝
                for key, value in src_file.tags.items():
                    dst_file.tags[key] = value
                
                dst_file.save()
                
        except Exception as e:
            print(f"Metadata copy error: {e}")
            # 不抛出异常，避免打断主流程

    def on_process(self, event):
        if self.audio_segment is None: 
            wx.MessageBox("音频正在加载中，请稍候...", "提示")
            return
        
        self.stop_playback_now()
        
        self.last_head_trim = self.start_mark
        self.last_tail_trim = self.duration_sec - self.end_mark
        self.has_processed_once = True
        
        start_ms = int(self.start_mark * 1000)
        end_ms = int(self.end_mark * 1000)
        seg = self.audio_segment[start_ms:end_ms]
        
        target_channels = 2 if self.cb_channels.GetValue() == "立体声" else 1
        if seg.channels != target_channels: seg = seg.set_channels(target_channels)
            
        target_rate = int(self.cb_sample_rate.GetValue())
        if seg.frame_rate != target_rate: seg = seg.set_frame_rate(target_rate)
            
        if self.chk_norm.GetValue():
            try:
                change_in_db = float(self.txt_db.GetValue()) - seg.dBFS
                seg = seg.apply_gain(change_in_db)
            except: pass

        fi = int(self.spin_fade_in.GetValue() * 1000)
        fo = int(self.spin_fade_out.GetValue() * 1000)
        if fi > 0: seg = seg.fade_in(fi)
        if fo > 0: seg = seg.fade_out(fo)
            
        save_dir = self.txt_save_path.GetValue()
        if not os.path.exists(save_dir):
            try: os.makedirs(save_dir)
            except: 
                wx.MessageBox("无法创建保存目录，请检查权限", "错误", wx.ICON_ERROR)
                return

        fname = os.path.splitext(os.path.basename(self.file_path))[0]
        fmt = self.cb_format.GetValue()
        out_path = os.path.join(save_dir, f"{fname}.{fmt}")
        
        try:
            # 1. 导出音频 (此时不带 Tags，因为 pydub/ffmpeg 对复杂 tag 支持不好)
            seg.export(out_path, format=fmt, bitrate=self.cb_bitrate.GetValue())
            
            # 2. === 调用 mutagen 完美拷贝元数据 ===
            self.copy_metadata(self.file_path, out_path)
            
            self.SetStatusText(f"已保存: {os.path.basename(out_path)}")
            
            current_idx = self.file_list.GetSelection()
            if current_idx != wx.NOT_FOUND:
                current_text = self.file_list.GetString(current_idx)
                if not current_text.startswith(self.MARKER_DONE):
                    self.file_list.SetString(current_idx, f"{self.MARKER_DONE}{current_text}")
            
            next_idx = current_idx + 1
            if next_idx < self.file_list.GetCount():
                self.file_list.SetSelection(next_idx)
                wx.CallAfter(self.on_file_selected, None)
            else:
                wx.MessageBox("所有文件处理完毕！", "完成", wx.ICON_INFORMATION)
            
        except Exception as e:
            wx.MessageBox(f"保存失败: {str(e)}", "错误", wx.ICON_ERROR)

if __name__ == '__main__':
    app = wx.App()
    frame = AudioEditorFrame()
    frame.Show()
    app.MainLoop()
    
    
