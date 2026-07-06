import time         
import paramiko     
import re           
import sqlite3      
import datetime     
import threading    
import subprocess   
from concurrent.futures import ThreadPoolExecutor, as_completed 
import tkinter as tk 
from tkinter import ttk, scrolledtext, messagebox 

# DATABASE
Database_Jenny = "frekuensi_jenny.db"

# FUNGSI PENGECEKAN JENIS IP LOCAL
def is_ip_local(ip: str) -> bool:       
    return (                            
        ip.startswith("192.168.") or    
        ip.startswith("10.")      or    
        ip.startswith("172.")           
    )

# FUNGSI PARSING FREKUENSI, SIGNAL, NOISE, DAN SNR
def parse_snr_from_line(line):                           
    match = re.search(                                   
        r'(\d{4,5})/[^\s]+\s+(-?\d+)\s+(-?\d+)\s+(\d+)', 
        line                                             
    )
    if match:                               
        return {                            
            "freq"  : match.group(1),       
            "RSSI": int(match.group(2)),    
            "noise" : int(match.group(3)),  
            "snr"   : int(match.group(4)),  
        }
    return None 

# FUNGSI MEMBACA KONDISI FREKUENSI AKTIF SEBELUM OPTIMASI
def parse_kondisi_awal(konfigurasi_text, hasil_scan_awal):
    freq_awal = None  
    snr_awal  = None  
    rssi_awal = None  
    noise_awal = None 

    freq_match = re.search(r'frequency[=:\s]+(\d{4,5})', konfigurasi_text)
    if freq_match:
        freq_awal = freq_match.group(1)
    if freq_awal and hasil_scan_awal:
        for line in hasil_scan_awal.split("\n"):
            data = parse_snr_from_line(line)
            if data and data["freq"] == freq_awal:
                snr_awal   = data["snr"]
                rssi_awal  = data["RSSI"]
                noise_awal = data["noise"]
                break
    return freq_awal, snr_awal, rssi_awal, noise_awal

# FUNGSI MENGUKUR LATENCY DAN PACKET LOSS VIA PING
def ukur_latency_packetloss(host):
    try:
        hasil = subprocess.run(
            ["ping", "-n", "10", host],
            capture_output=True, text=True, timeout=30
        )
        output = hasil.stdout
        latency_match = re.search(r'Average\s*=\s*(\d+)ms', output)
        latency_ms    = float(latency_match.group(1)) if latency_match else None
        loss_match  = re.search(r'\((\d+)%\s+loss\)', output)
        packet_loss = float(loss_match.group(1)) if loss_match else None
        return latency_ms, packet_loss
    except Exception:
        return None, None

# FUNGSI MENGUKUR THROUGHPUT VIA WIRELESS MONITOR MIKROTIK
def ukur_throughput(cfg, interface="wlan1"):
    try:
        hasil = ssh_execute_command(
            f"interface wireless monitor {interface} once", cfg)
        if not hasil:
            return None, None
        def ke_mbps(nilai_str, satuan):
            nilai = float(nilai_str)
            s = (satuan or "").upper()
            if s == "K": return round(nilai / 1000, 2)
            if s == "G": return round(nilai * 1000, 2)
            return round(nilai, 2)
        
        tx_match = re.search(r'tx-rate:\s*([\d.]+)(M|k|G)?bps', hasil, re.IGNORECASE)
        rx_match = re.search(r'rx-rate:\s*([\d.]+)(M|k|G)?bps', hasil, re.IGNORECASE)
        tx_mbps = ke_mbps(tx_match.group(1), tx_match.group(2)) if tx_match else None
        rx_mbps = ke_mbps(rx_match.group(1), rx_match.group(2)) if rx_match else None

        return tx_mbps, rx_mbps
    except Exception:
        return None, None

# FUNGSI FORMAT NILAI PENGUKURAN UNTUK TAMPILAN LOG
def fmt(nilai, satuan):
    if nilai is None:
        return "N/A"
    return f"{nilai}{satuan}"

# FUNGSI KONEKSI SSH KE ROUTER IP PUBLIC
def ssh_execute_public(command, hostname, port, username, password):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname, port=port, username=username, password=password, timeout=10)
        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode("utf-8")
        client.close()
        return output
    except paramiko.AuthenticationException:
        return None
    except Exception as e:
        print("SSH Error (public):", e)
        return None

# FUNGSI KONEKSI SSH KE ROUTER IP LOCAL
def ssh_execute_local(command, hostname, username, password):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname, username=username, password=password, timeout=10)
        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode("utf-8")
        client.close()
        return output
    except paramiko.AuthenticationException:
        return None
    except Exception as e:
        print("SSH Error (local):", e)
        return None

# FUNGSI OTOMATIS MEMILIH KONEKSI SSH PUBLIC ATAU LOCAL
def ssh_execute_command(command, cfg):
    if cfg["jenis"] == "public":                    
        return ssh_execute_public(                  
            command, cfg["hostname"], cfg["port"],  
            cfg["username"], cfg["password"]        
        )
    else:
        return ssh_execute_local(           
            command, cfg["hostname"],       
            cfg["username"], cfg["password"]
        )

# FUNGSI INISIALISASI DATABASE DAN TABEL SISTEM
def init_database():
    conn   = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()

    # Membuat tabel data router IP Public
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS router_public (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            label     TEXT    NOT NULL,
            hostname  TEXT    NOT NULL,
            port      INTEGER NOT NULL DEFAULT 22,
            username  TEXT    NOT NULL DEFAULT '',
            password  TEXT    NOT NULL DEFAULT '',
            interface TEXT    NOT NULL DEFAULT 'wlan1',
            aktif     INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Membuat tabel data router IP Local
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS router_local (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            label     TEXT    NOT NULL,
            hostname  TEXT    NOT NULL,
            username  TEXT    NOT NULL DEFAULT '',
            password  TEXT    NOT NULL DEFAULT '',
            interface TEXT    NOT NULL DEFAULT 'wlan1',
            aktif     INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Membuat tabel penyimpanan hasil optimasi frekuensi
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hasil_optimasi (
            id_record           INTEGER PRIMARY KEY AUTOINCREMENT,
            router_label        TEXT    NOT NULL DEFAULT 'Router',
            jenis_ip            TEXT    NOT NULL DEFAULT 'public',
            timestamp           TEXT    NOT NULL,
            frekuensi_sebelum   TEXT,
            snr_sebelum         REAL,
            rssi_sebelum        REAL,
            noise_sebelum       REAL,
            latency_sebelum     REAL,
            packetloss_sebelum  REAL,
            txrate_sebelum      REAL,
            rxrate_sebelum      REAL,
            frekuensi_mhz       INTEGER NOT NULL,
            snr_value           REAL    NOT NULL,
            signal_strength     REAL    NOT NULL,
            noise_floor         REAL    NOT NULL,
            delta_snr           REAL,
            latency_sesudah     REAL,
            packetloss_sesudah  REAL,
            txrate_sesudah      REAL,
            rxrate_sesudah      REAL,
            action_taken        TEXT    NOT NULL,
            metode              TEXT    NOT NULL,
            durasi_detik        REAL    NOT NULL DEFAULT 0
        )
    """)

    # Memeriksa apakah tabel router_public masih kosong
    cursor.execute("SELECT COUNT(*) FROM router_public")
    if cursor.fetchone()[0] == 0:
        # Menyiapkan data awal router public
        seed_public = [
            ("R1 Pub", "222.124.22.44", 8211, "", "", "wlan1", 1),
            ("R2 Pub", "222.124.22.44", 8212, "", "", "wlan1", 1),
            ("R3 Pub", "222.124.22.44", 8213, "", "", "wlan1", 1),
        ]
        cursor.executemany("""
            INSERT INTO router_public
                (label, hostname, port, username, password, interface, aktif)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, seed_public)

    # Memeriksa apakah tabel router_local masih kosong
    cursor.execute("SELECT COUNT(*) FROM router_local")
    if cursor.fetchone()[0] == 0:
        # Menyiapkan data awal router local
        seed_local = [
            ("R1 Loc", "192.168.10.11", "", "", "wlan1", 1),
            ("R2 Loc", "192.168.10.12", "", "", "wlan1", 1),
            ("R3 Loc", "192.168.10.13", "", "", "wlan1", 1),
        ]
        cursor.executemany("""
            INSERT INTO router_local
                (label, hostname, username, password, interface, aktif)
            VALUES (?, ?, ?, ?, ?, ?)
        """, seed_local)

    conn.commit()
    conn.close()

# FUNGSI MEMUAT DATA KONFIGURASI ROUTER DARI DATABASE
def load_router_configs():
    conn   = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, label, hostname, port, username, password, interface
        FROM router_public WHERE aktif = 1 ORDER BY id
    """)
    rows_public = cursor.fetchall()
    cursor.execute("""
        SELECT id, label, hostname, username, password, interface
        FROM router_local WHERE aktif = 1 ORDER BY id
    """)
    rows_local = cursor.fetchall()
    conn.close()

    configs = []
    for r in rows_public:
        configs.append({
            "db_id"    : r[0],
            "label"    : r[1],
            "hostname" : r[2],
            "port"     : r[3],
            "username" : r[4],
            "password" : r[5],
            "interface": r[6],
            "jenis"    : "public",
        })

    for r in rows_local:
        configs.append({
            "db_id"    : r[0],
            "label"    : r[1],
            "hostname" : r[2],
            "port"     : None,
            "username" : r[3],
            "password" : r[4],
            "interface": r[5],
            "jenis"    : "local",
        })
    return configs

# FUNGSI MENAMBAHKAN DATA ROUTER PUBLIC KE DATABASE
def tambah_router_public(label, hostname, port, username, password, interface):
    conn = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO router_public (label, hostname, port, username, password, interface, aktif)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (label, hostname, port, username, password, interface))

    conn.commit();  
    conn.close()    

# FUNGSI MEMPERBARUI DATA ROUTER PUBLIC DI DATABASE
def update_router_public(db_id, label, hostname, port, username, password, interface):
    conn = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE router_public SET label=?, hostname=?, port=?, username=?, password=?, interface=?
        WHERE id=?
    """, (label, hostname, port, username, password, interface, db_id))

    conn.commit();  
    conn.close()    

# FUNGSI MENGHAPUS DATA ROUTER PUBLIC SECARA LOGIS (SOFT DELETE)
def hapus_router_public(db_id):
    conn = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()
    cursor.execute("UPDATE router_public SET aktif=0 WHERE id=?", (db_id,))

    conn.commit(); 
    conn.close()    

# FUNGSI MENAMBAHKAN DATA ROUTER LOCAL KE DATABASE
def tambah_router_local(label, hostname, username, password, interface):
    conn = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO router_local (label, hostname, username, password, interface, aktif)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (label, hostname, username, password, interface))

    conn.commit();  
    conn.close()   

# FUNGSI MEMPERBARUI DATA ROUTER LOCAL DI DATABASE
def update_router_local(db_id, label, hostname, username, password, interface):
    conn = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE router_local SET label=?, hostname=?, username=?, password=?, interface=?
        WHERE id=?
    """, (label, hostname, username, password, interface, db_id))

    conn.commit();  
    conn.close()    

# FUNGSI MENGHAPUS DATA ROUTER LOCAL SECARA LOGIS (SOFT DELETE)
def hapus_router_local(db_id):
    conn = sqlite3.connect(Database_Jenny)
    cursor = conn.cursor()
    cursor.execute("UPDATE router_local SET aktif=0 WHERE id=?", (db_id,))

    conn.commit(); 
    conn.close()    

# FUNGSI MENYIMPAN HASIL OPTIMASI KE DATABASE
def simpan_ke_database(router_label, jenis_ip, frekuensi, snr, signal,
                       noise, action, metode, durasi=0, freq_sblm=None,
                       snr_sblm=None, rssi_sblm=None, noise_sblm=None,
                       latency_sblm=None, loss_sblm=None, tx_sblm=None,
                       rx_sblm=None, latency_sdh=None,loss_sdh=None,
                       tx_sdh=None, rx_sdh=None):
    try:
        conn = sqlite3.connect(Database_Jenny)
        cursor = conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        delta_snr = round(snr - snr_sblm, 2) if snr_sblm is not None else None
        cursor.execute("""
            INSERT INTO hasil_optimasi (
                router_label, jenis_ip, timestamp, frekuensi_sebelum, snr_sebelum,
                rssi_sebelum, noise_sebelum, latency_sebelum, packetloss_sebelum,
                txrate_sebelum, rxrate_sebelum, frekuensi_mhz, snr_value, signal_strength,
                noise_floor, delta_snr, latency_sesudah, packetloss_sesudah, txrate_sesudah,
                rxrate_sesudah, action_taken, metode, durasi_detik
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            router_label, jenis_ip, timestamp, freq_sblm, snr_sblm, rssi_sblm, noise_sblm,
            latency_sblm, loss_sblm, tx_sblm, rx_sblm, frekuensi, snr, signal, noise, delta_snr,
            latency_sdh, loss_sdh, tx_sdh, rx_sdh, action, metode, durasi
        ))

        conn.commit()  
        conn.close()   
        print(f"[DB] Data {router_label} berhasil disimpan")

    except Exception as e:
        print("[DB ERROR]", e) 

# KELAS FORM TAMBAH DAN EDIT DATA ROUTER
class Aplikasi_Jenny(tk.Toplevel):
    def __init__(self, parent, cfg=None):
        super().__init__(parent)
        self.result = None
        self.cfg    = cfg
        self.title("Tambah Router" if cfg is None else "Edit Router")
        self.configure(bg="#FFFFFF")
        self.resizable(False, False)
        self.grab_set()
        self._build()
        self.transient(parent)
        self.wait_window()

    def _build(self):
        pad = {"padx": 12, "pady": 6}
        entry_style = {
            "bg": "#A1A1A1", "fg": "#FFFFFF",
            "insertbackground": "#00FF88",
            "font": ("Arial", 9),
            "relief": "flat", "width": 28
        }
        lbl_style = {
            "bg": "#FFFFFF", "fg": "#000000",
            "font": ("Arial", 9, "bold")
        }
        cfg = self.cfg or {}

        tk.Label(self, text="Label Router", **lbl_style).grid(row=0, column=0, sticky="w", **pad) 
        self.v_label = tk.StringVar(value=cfg.get("label", "")) 
        tk.Entry(self, textvariable=self.v_label, **entry_style).grid(row=0, column=1, **pad)

        tk.Label(self, text="Hostname / IP", **lbl_style).grid(row=1, column=0, sticky="w", **pad) 
        self.v_hostname = tk.StringVar(value=cfg.get("hostname", "")) 
        self.v_hostname.trace_add("write", self._on_hostname_change) 
        tk.Entry(self, textvariable=self.v_hostname, **entry_style).grid(row=1, column=1, **pad)

        self.lbl_port = tk.Label(self, text="Port SSH", **lbl_style) 
        self.v_port   = tk.StringVar(value=str(cfg.get("port", "22") or "22")) 
        self.ent_port = tk.Entry(self, textvariable=self.v_port, **entry_style) 

        tk.Label(self, text="Username", **lbl_style).grid(row=3, column=0, sticky="w", **pad) 
        self.v_username = tk.StringVar(value=cfg.get("username", "")) 
        tk.Entry(self, textvariable=self.v_username, **entry_style).grid(row=3, column=1, **pad) 

        tk.Label(self, text="Password", **lbl_style).grid(row=4, column=0, sticky="w", **pad)
        self.v_password = tk.StringVar(value=cfg.get("password", "")) 
        tk.Entry(self, textvariable=self.v_password, show="*", **entry_style).grid(row=4, column=1, **pad) 

        tk.Label(self, text="Interface", **lbl_style).grid(row=5, column=0, sticky="w", **pad) 
        self.v_interface = tk.StringVar(value=cfg.get("interface", "wlan1")) 
        tk.Entry(self, textvariable=self.v_interface, **entry_style).grid(row=5, column=1, **pad)

        self.lbl_info = tk.Label(self, text="", bg="#FFFFFF", font=("Arial", 8, "italic")) 
        self.lbl_info.grid(row=6, column=0, columnspan=2, pady=(0, 4)) 

        btn_frame = tk.Frame(self, bg="#FFFFFF") 
        btn_frame.grid(row=7, column=0, columnspan=2, pady=10)
        
        tk.Button(btn_frame, text="Simpan",
                  bg="#E2E2E2", fg="#000000",
                  font=("Arial", 9, "bold"),
                  relief="flat", padx=12, pady=6,
                  command=self._simpan).pack(side="left", padx=8)
        
        tk.Button(btn_frame, text="Batal",
                  bg="#E2E2E2", fg="#000000",
                  font=("Consolas", 9, "bold"),
                  relief="flat", padx=12, pady=6,
                  command=self.destroy).pack(side="left", padx=8) 

        self._on_hostname_change()

    def _on_hostname_change(self, *_):
        ip    = self.v_hostname.get().strip() 
        local = is_ip_local(ip)               
        pad   = {"padx": 12, "pady": 6}       
        if local:                             
            self.lbl_port.grid_remove()
            self.ent_port.grid_remove()
            self.lbl_info.config(
                text="✔ IP Local terdeteksi — SSH tanpa port",
                fg="#000000")
        else:
            self.lbl_port.grid(row=2, column=0, sticky="w", **pad)
            self.ent_port.grid(row=2, column=1, **pad)
            if ip == "":
                self.lbl_info.config(
                    text="Masukkan IP — Port akan tampil untuk IP Public",
                    fg="#000000"
                    )
            else:
                self.lbl_info.config(text="✔ IP Public terdeteksi — Isi Port SSH",fg="#000000")

    # Memvalidasi dan menyimpan data router dari form
    def _simpan(self):
        label     = self.v_label.get().strip()
        hostname  = self.v_hostname.get().strip()
        username  = self.v_username.get().strip()
        password  = self.v_password.get().strip()
        interface = self.v_interface.get().strip()
        if not label or not hostname or not interface:
            messagebox.showwarning("Input Error",
                "Label, Hostname, dan Interface wajib diisi.", parent=self)
            return

        local = is_ip_local(hostname)
        if local:
            port = None
        else:
            port_text = self.v_port.get().strip()
            port = 22 if port_text == "" else None
            if port is None:
                try:
                    port = int(port_text)
                except ValueError:
                    messagebox.showwarning("Input Error", 
                        "Port SSH harus berupa angka.", parent=self)
                    return
        self.result = {
            "label": label, "hostname": hostname, "port": port,
            "username": username, "password": password, "interface": interface,
            "jenis": "local" if local else "public",
        }
        self.destroy()

# === APLIKASI UTAMA KODE TKINTER === #
class AplikasiOptimasiJenny(tk.Tk):
    def __init__(self):
        super().__init__() 
        self.title("Sistem Otomasi Optimasi Frekuensi Nirkabel Mikrotik - Jenny Marpaung")
        self.geometry("1280x960")
        self.configure(bg="#ffffff")
        self.resizable(True, True)
        init_database()
        self.router_configs = load_router_configs()
        self._setup_styles()
        self._build_ui()

    # FUNGSI PENGATURAN TEMA DAN STYLE ANTARMUKA (GUI)
    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TLabelframe",
                        background="#FFFFFF",         
                        foreground="#000000",         
                        font=("Arial", 10, "bold"),
                        relief="flat")                
        style.configure("TLabelframe.Label",
                        background="#FFFFFF",
                        foreground="#000000",
                        font=("Arial", 10, "bold"))
        style.configure("TLabel",
                        background="#FFFFFF",
                        foreground="#E0E0E0",
                        font=("Arial", 10))
        style.configure("Header.TLabel",
                        background="#FFFFFF",
                        foreground="#000000",
                        font=("Arial", 10, "bold"))
        style.configure("Treeview",
                        background="#7499C6",       
                        foreground="#000000",       
                        fieldbackground="#FFFFFF",  
                        font=("Arial", 9),
                        rowheight=26)                 
        style.configure("Treeview.Heading",
                        background="#FFFFFF",
                        foreground="#000000",
                        font=("arial", 9, "bold"))
        style.map("Treeview",
                  background=[("selected", "#B5B5B5")],
                  foreground=[("selected", "#0F3460")])
        style.configure("TNotebook",
                        background="#FFFFFF",
                        tabmargins=[2, 5, 2, 0])
        style.configure("TNotebook.Tab",
                        foreground="#000000",
                        font=("Arial", 7, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", "#0D7377")],
                  foreground=[("selected", "#FFFFFF")])

    # FUNGSI PEMBANGUN ANTARMUKA UTAMA APLIKASI (BUILD UI)
    def _build_ui(self):
        frame_header = tk.Frame(self, bg="#FFFFFF", pady=4)
        frame_header.pack(fill="x", padx=15)
        ttk.Label(frame_header,
                  text="SISTEM OTOMASI OPTIMASI FREKUENSI NIRKABEL MIKROTIK — JENNY MARPAUNG",
                  style="Header.TLabel").pack(side="left")
        tk.Frame(self, bg="#00D4FF", height=1).pack(fill="x", padx=15)

        frame_mgr = ttk.LabelFrame(self, text=" DAFTAR ROUTER ")
        frame_mgr.pack(fill="x", padx=15, pady=(4, 0))

        self.router_notebook = ttk.Notebook(frame_mgr)
        self.router_notebook.pack(fill="x", padx=5, pady=3)

        tab_public = tk.Frame(self.router_notebook, bg="#FFFFFF")
        self.router_notebook.add(tab_public, text="IP PUBLIC")

        frame_pub_tree = tk.Frame(tab_public)
        frame_pub_tree.pack(fill="x",     
                            padx=8,       
                            pady=(4, 0))  

        cols_pub = ("Label", "Hostname", "Port", "Username", "Interface", "Status", "✓")
        self.tree_router_public = ttk.Treeview(
            frame_pub_tree, columns=cols_pub, show="headings", height=3)
        for col, w in zip(cols_pub, [120, 165, 65, 120, 95, 65, 40]):
            self.tree_router_public.heading(col, text=col)
            self.tree_router_public.column(col, width=w, anchor="center")
        self.tree_router_public.pack(fill="x", expand=True)
        self.tree_router_public.bind(
            "<ButtonRelease-1>",
            lambda e: self._toggle_check(e, "public")
        )

        tab_local = tk.Frame(self.router_notebook, bg="#FFFFFF")
        self.router_notebook.add(tab_local, text="IP LOCAL")

        frame_loc_tree = tk.Frame(tab_local)
        frame_loc_tree.pack(fill="x", padx=8, pady=(4, 0))

        cols_loc = ("Label", "Hostname", "Username", "Interface", "Status", "✓")
        self.tree_router_local = ttk.Treeview(
            frame_loc_tree, columns=cols_loc, show="headings", height=3)
        for col, w in zip(cols_loc, [130, 185, 130, 110, 65, 40]):
            self.tree_router_local.heading(col, text=col)
            self.tree_router_local.column(col, width=w, anchor="center")
        self.tree_router_local.pack(fill="x", expand=True)
        self.tree_router_local.bind(
            "<ButtonRelease-1>",
            lambda e: self._toggle_check(e, "local")
        )

        # TOMBOL MANAJEMEN ROUTER
        btn_mgr = tk.Frame(frame_mgr, bg="#FFFFFF", pady=3)
        btn_mgr.pack(fill="x", padx=8)
        ttk.Button(btn_mgr, text="＋ Tambah Router", style="Action.TButton", command=self.mgr_tambah).pack(side="left", padx=4)
        ttk.Button(btn_mgr, text="Edit Router", style="Primary.TButton", command=self.mgr_edit).pack(side="left", padx=4)
        ttk.Button(btn_mgr, text="✕ Hapus Router", style="Danger.TButton",command=self.mgr_hapus).pack(side="left", padx=4)

        self.refresh_daftar_router()

        frame_ops = ttk.LabelFrame(self, text=" OPERASI ROUTER ")
        frame_ops.pack(fill="x", padx=8, pady=(2, 0))

        ops_container = tk.Frame(frame_ops, bg="#FFFFFF")
        ops_container.pack(fill="x", padx=8, pady=2)

        # OPERASI ROUTER IP PUBLIC
        frame_ops_pub = tk.LabelFrame(ops_container,
                                       text="IP PUBLIC",
                                       bg="#FFFFFF", fg="#000000",
                                       font=("Arial", 9, "bold"),
                                       relief="ridge", bd=1)
        frame_ops_pub.pack(fill="x", pady=(0, 2))

        ttk.Button(frame_ops_pub, text="SCAN SEMUA IP PUBLIC",
                   style="Public.TButton",
                   command=self.op_scan_semua_public).pack(
                       side="left", padx=2, pady=2)
        ttk.Button(frame_ops_pub, text="OPTIMASI SEMUA IP PUBLIC",
                   style="Action.TButton",
                   command=self.op_optimasi_semua_public).pack(
                       side="left", padx=2, pady=2)
        ttk.Button(frame_ops_pub, text="☑ SCAN PUBLIC TERPILIH",
                   style="Selected.TButton",
                   command=self.op_scan_terpilih_public).pack(
                       side="left", padx=2, pady=2)
        ttk.Button(frame_ops_pub, text="☑ OPTIMASI PUBLIC TERPILIH",
                   style="Selected.TButton",
                   command=self.op_optimasi_terpilih_public).pack(
                       side="left", padx=2, pady=2)

        # OPERASI ROUTER IP LOCAL
        frame_ops_loc = tk.LabelFrame(ops_container,
                                       text="IP LOCAL",
                                       bg="#FFFFFF", fg="#000000",
                                       font=("Arial", 9, "bold"),
                                       relief="ridge", bd=1)
        frame_ops_loc.pack(fill="x", pady=(0, 2))

        ttk.Button(frame_ops_loc, text="SCAN SEMUA IP LOCAL",
                   style="Local.TButton",
                   command=self.op_scan_semua_local).pack(
                       side="left", padx=2, pady=2)
        ttk.Button(frame_ops_loc, text="OPTIMASI SEMUA IP LOCAL",
                   style="Manual.TButton",
                   command=self.op_optimasi_semua_local).pack(
                       side="left", padx=2, pady=2)
        ttk.Button(frame_ops_loc, text="☑ SCAN LOCAL TERPILIH",
                   style="Selected.TButton",
                   command=self.op_scan_terpilih_local).pack(
                       side="left", padx=2, pady=2)
        ttk.Button(frame_ops_loc, text="☑ OPTIMASI LOCAL TERPILIH",
                   style="Selected.TButton",
                   command=self.op_optimasi_terpilih_local).pack(
                       side="left", padx=2, pady=2)

        frame_ops_util = tk.Frame(frame_ops, bg="#FFFFFF")
        frame_ops_util.pack(fill="x", padx=8, pady=(0, 2))

        ttk.Button(frame_ops_util, text="Bersihkan Semua Log",
                   style="Primary.TButton",
                   command=self.bersihkan_semua_log).pack(side="right", padx=4)

        frame_log_outer = ttk.LabelFrame(self, text=" LOG PROSES PER ROUTER ")
        frame_log_outer.pack(fill="both", expand=True, padx=15, pady=(0, 4))

        self.log_parent_notebook = ttk.Notebook(frame_log_outer)
        self.log_parent_notebook.pack(fill="both", expand=True, padx=5, pady=5)

        tab_log_public = tk.Frame(self.log_parent_notebook, bg="#FFFFFF")
        self.log_parent_notebook.add(tab_log_public, text="IP PUBLIC")
        self.notebook_public = ttk.Notebook(tab_log_public)
        self.notebook_public.pack(fill="both", expand=True)

        tab_log_local = tk.Frame(self.log_parent_notebook, bg="#FFFFFF")
        self.log_parent_notebook.add(tab_log_local, text="IP LOCAL")
        self.notebook_local = ttk.Notebook(tab_log_local)
        self.notebook_local.pack(fill="both", expand=True)

        self.log_widgets = {}
        self._rebuild_tabs()

        self.status_var = tk.StringVar(value="Siap.")
        tk.Label(self, textvariable=self.status_var,
                 bg="#0F3460", fg="#00D4FF",
                 font=("Arial", 9), anchor="w",
                 padx=10).pack(fill="x", side="bottom")

    # FUNGSI TOGGLE CHECKBOX ROUTER
    def _toggle_check(self, event, jenis: str):
        tree = self.tree_router_public if jenis == "public" else self.tree_router_local
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col_id = tree.identify_column(event.x)
        cols       = tree["columns"]
        last_col   = f"#{len(cols)}"
        if col_id != last_col:
            return
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        vals      = list(tree.item(row_id, "values"))
        vals[-1]  = "☑" if vals[-1] == "☐" else "☐"
        tree.item(row_id, values=vals)

    # FUNGSI MEMBANGUN ULANG TAB LOG ROUTER
    def _rebuild_tabs(self):
        for tab in self.notebook_public.tabs():
            self.notebook_public.forget(tab)
        for tab in self.notebook_local.tabs():
            self.notebook_local.forget(tab)
        self.log_widgets.clear()

        for cfg in self.router_configs:
            jenis     = cfg["jenis"]
            jenis_tag = "PUBLIC" if jenis == "public" else "LOCAL"
            nb        = self.notebook_public if jenis == "public" else self.notebook_local
            tab_frame = tk.Frame(nb, bg="#FFFFFF")
            nb.add(tab_frame, text=f"  {cfg['label']}  ")
            btn_tab = tk.Frame(tab_frame, bg="#0F3460", pady=5)
            btn_tab.pack(fill="x", padx=5, pady=(5, 0))

            if jenis == "public":
                info_txt = (f"  {cfg['label']}  [{jenis_tag}]"
                            f"  —  {cfg['hostname']}:{cfg['port']}"
                            f"  |  {cfg['interface']}")
            else:
                info_txt = (f"  {cfg['label']}  [{jenis_tag}]"
                            f"  —  {cfg['hostname']}"
                            f"  |  {cfg['interface']}")

            info_color = "#FFFFFF" if jenis == "public" else "#050706"
            tk.Label(btn_tab, text=info_txt,
                     bg="#0F3460", fg=info_color,
                     font=("Arial", 9, "bold")).pack(side="left", padx=8)

            # Membuat perintah Scan Router
            def make_scan_cmd(c=cfg):
                return lambda: threading.Thread(
                    target=self._task_scan, args=(c,), daemon=True).start()
            # Membuat perintah Optimasi Router
            def make_optimasi_cmd(c=cfg):
                return lambda: threading.Thread(
                    target=self._run_single_optimasi, args=(c,), daemon=True).start()
            # Membuat perintah Bersihkan Log
            def make_clear_cmd(lbl=cfg["label"]):
                return lambda: self.log_widgets[lbl].delete("1.0", tk.END)

            tk.Button(btn_tab, text="SCAN",
                      bg="#50F6FF", fg="#040606",
                      font=("Arial", 7, "bold"),
                      relief="flat", padx=5, pady=4, cursor="hand2",
                      command=make_scan_cmd()).pack(side="right", padx=4)
            tk.Button(btn_tab, text="OPTIMASI & TERAPKAN",
                      bg="#0D7377", fg="#FFFFFF",
                      font=("Arial", 7, "bold"),
                      relief="flat", padx=5, pady=4, cursor="hand2",
                      command=make_optimasi_cmd()).pack(side="right", padx=4)
            tk.Button(btn_tab, text="Bersihkan Log",
                      bg="#0C0C2B", fg="#FFFFFF",
                      font=("Arial", 7),
                      relief="flat", padx=5, pady=4, cursor="hand2",
                      command=make_clear_cmd()).pack(side="right", padx=4)

            log_widget = scrolledtext.ScrolledText(
                tab_frame, wrap=tk.WORD,
                bg="#F2F2F2", fg="#00FF88",
                insertbackground="#00FF88",
                font=("Consolas", 9),
                relief="flat", padx=8, pady=8
            )
            log_widget.pack(fill="both", expand=True) 
            log_widget.tag_config("info",    foreground="#000000")
            log_widget.tag_config("success", foreground="#000000")
            log_widget.tag_config("warning", foreground="#FF0000")
            log_widget.tag_config("optimal", foreground="#FF4444", font=("Arial", 9, "bold"))
            log_widget.tag_config("header",  foreground="#5D00FF")
            log_widget.tag_config("info_bold", foreground="#000000", font=("Consolas", 9, "bold")
)

            self.log_widgets[cfg["label"]] = log_widget
            port_info = f":{cfg['port']}" if jenis == "public" else ""
            self._log(cfg["label"],
                      f"Tab siap — {cfg['hostname']}{port_info} | {cfg['interface']} [{jenis_tag}]",
                      "info")

    # FUNGSI MEMPERBARUI DAFTAR ROUTER
    def refresh_daftar_router(self):
        self.router_configs = load_router_configs()

        for row in self.tree_router_public.get_children():
            self.tree_router_public.delete(row)
        for row in self.tree_router_local.get_children():
            self.tree_router_local.delete(row)
        for cfg in self.router_configs:
            uname = cfg["username"] if cfg["username"] else "(kosong)"
            if cfg["jenis"] == "public":
                self.tree_router_public.insert("", "end", values=(
                    cfg["label"], cfg["hostname"],
                    cfg["port"], uname, cfg["interface"], "Aktif", "☐"
                ), iid=str(cfg["db_id"]))
            else:
                self.tree_router_local.insert("", "end", values=(
                    cfg["label"], cfg["hostname"],
                    uname, cfg["interface"], "Aktif", "☐"
                ), iid=str(cfg["db_id"]))

    # FUNGSI MENGAMBIL ROUTER YANG DIPILIH
    def _get_checked_configs(self, jenis: str):
        tree   = self.tree_router_public if jenis == "public" else self.tree_router_local
        result = []
        for cfg in self.router_configs:
            if cfg["jenis"] != jenis:
                continue
            row = tree.item(str(cfg["db_id"]), "values")
            if row and row[-1] == "☑":
                result.append(cfg)
        return result

    # FUNGSI MENGAMBIL ROUTER YANG DIPILIH
    def _get_selected_cfg(self):
        tab_idx = self.router_notebook.index(self.router_notebook.select())           
        tree    = self.tree_router_public if tab_idx == 0 else self.tree_router_local 
        sel     = tree.selection()                                                                                                      
        if not sel:
            messagebox.showwarning("Pilih Router",
                "Klik baris router yang ingin diproses terlebih dahulu.")
            return None
        db_id = int(sel[0])                            
        jenis = "public" if tab_idx == 0 else "local"  
        cfg   = next((r for r in self.router_configs
                      if r["db_id"] == db_id and r["jenis"] == jenis), None)
        if cfg is None:
            messagebox.showwarning("Error", "Router tidak ditemukan.")
        return cfg 

    # FUNGSI MENAMBAHKAN ROUTER
    def mgr_tambah(self):
        dlg = Aplikasi_Jenny(self)
        if dlg.result:          
            r = dlg.result       
            if r["jenis"] == "public":
                tambah_router_public(r["label"], r["hostname"], r["port"],
                                     r["username"], r["password"], r["interface"])
            else:
                tambah_router_local(r["label"], r["hostname"],
                                    r["username"], r["password"], r["interface"])
            self.refresh_daftar_router()
            self._rebuild_tabs()        
            self.set_status(f"Router '{r['label']}' berhasil ditambahkan.") 

    # FUNGSI MENGUBAH DATA ROUTER
    def mgr_edit(self):
        cfg = self._get_selected_cfg()
        if cfg is None:
            return
        dlg = Aplikasi_Jenny(self, cfg=cfg)
        if dlg.result:
            r = dlg.result
            if r["jenis"] == "public":
                update_router_public(cfg["db_id"], r["label"], r["hostname"], r["port"],
                                     r["username"], r["password"], r["interface"])
            else:
                update_router_local(cfg["db_id"], r["label"], r["hostname"],
                                    r["username"], r["password"], r["interface"])
            self.refresh_daftar_router()
            self._rebuild_tabs()      
            self.set_status(f"Router '{r['label']}' berhasil diperbarui.")

    # FUNGSI MENGHAPUS ROUTER
    def mgr_hapus(self):
        cfg = self._get_selected_cfg() 
        if cfg is None:
            return
        if not messagebox.askyesno("Konfirmasi Hapus",
                                   f"Hapus router '{cfg['label']}'?"):
            return
        if cfg["jenis"] == "public":
            hapus_router_public(cfg["db_id"])
        else:
            hapus_router_local(cfg["db_id"])
        self.refresh_daftar_router()
        self._rebuild_tabs()
        self.set_status(f"Router '{cfg['label']}' berhasil dihapus.")

    # FUNGSI MENAMPILKAN LOG PROSES
    def _log(self, router_label, message, tag="success"):
        widget = self.log_widgets.get(router_label)
        if widget is None:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        widget.insert(tk.END, f"[{ts}] {message}\n", tag)
        widget.see(tk.END)

    # FUNGSI LOG THREAD-SAFE
    def log(self, router_label, message, tag="success"):
        self.after(0, self._log, router_label, message, tag)

    # FUNGSI MENGUBAH STATUS APLIKASI
    def set_status(self, text):
        self.after(0, self.status_var.set, text)

    # FUNGSI MEMBERSIHKAN SEMUA LOG
    def bersihkan_semua_log(self):
        for widget in self.log_widgets.values():
            widget.delete("1.0", tk.END)

    # FUNGSI BERPINDAH KE TAB LOG
    def _switch_log_tab(self, jenis: str):
        idx = 0 if jenis == "public" else 1 
        self.after(0, self.log_parent_notebook.select, idx) 

    # FUNGSI SCAN FREKUENSI ROUTER
    def _task_scan(self, cfg):
        label     = cfg["label"]
        host      = cfg["hostname"]
        interface = cfg["interface"]
        jenis_tag = "PUBLIC" if cfg["jenis"] == "public" else "LOCAL"
        if not cfg["username"] or not cfg["password"]:
            self.log(label, "Username/password belum diisi! Scan dibatalkan.", "warning")
            return

        self.log(label, "=" * 50, "header")
        self.log(label, f"SCAN FREKUENSI — {label} [{jenis_tag}]", "header")
        self.log(label, "=" * 50, "header")
        if cfg["jenis"] == "public":
            self.log(label, f"Target   : {host}  Port: {cfg['port']}", "info")
        else:
            self.log(label, f"Target   : {host}  (tanpa port)", "info")
        self.log(label, f"Interface: {interface}", "info")

        # Membaca konfigurasi awal router
        konfigurasi = ssh_execute_command(
            f"interface wireless print detail where name={interface}", cfg)
        if konfigurasi is None:
            self.log(label, "Gagal koneksi SSH!", "warning")
            return
        self.log(label, konfigurasi.strip(), "success")

        # Melakukan scan frekuensi
        self.log(label, "Melakukan scan frekuensi (tunggu ±5 detik)...", "info")
        hasil_scan = ssh_execute_command(
            f"interface wireless scan {interface} duration=5", cfg)
        if not hasil_scan or hasil_scan.strip() == "":
            self.log(label, "Tidak ada hasil scan.", "warning")
            return
        
        self.log(label, "Hasil Scan:", "info")
        best_snr_scan = -999
        best_line     = ""
        parsed_lines  = []
        for line in hasil_scan.split("\n"):
            if line.strip() == "":
                continue
            data    = parse_snr_from_line(line)    
            snr_val = data["snr"] if data else None
            parsed_lines.append((line, snr_val))   
            if snr_val is not None and snr_val > best_snr_scan:
                best_snr_scan = snr_val
                best_line     = line

        for line, snr_val in parsed_lines:
            if line == best_line and best_snr_scan > -999:
                self.log(label,
                         f"★ {line.strip()}  ← SNR TERTINGGI ({best_snr_scan} dB)",
                         "optimal")
            else:
                self.log(label, line.rstrip(), "success")
        self.log(label, f"Scan {label} selesai.", "info")

    # FUNGSI OPTIMASI FREKUENSI ROUTER
    def _task_optimasi(self, cfg):
        label     = cfg["label"]
        host      = cfg["hostname"]
        interface = cfg["interface"]
        jenis     = cfg["jenis"]
        jenis_tag = "PUBLIC" if jenis == "public" else "LOCAL"
        if not cfg["username"] or not cfg["password"]:
            self.log(label, "Username/password belum diisi! Optimasi dibatalkan.", "warning")
            return

        waktu_mulai = time.time()
        self.log(label, "=" * 50, "header")
        self.log(label, f"OPTIMASI FREKUENSI — {label} [{jenis_tag}]", "header")
        self.log(label, "=" * 50, "header")
        if jenis == "public":
            self.log(label, f"Target   : {host}  Port: {cfg['port']}", "info")
        else:
            self.log(label, f"Target   : {host}  (tanpa port)", "info")
        self.log(label, f"Interface: {interface}", "info")
        self.log(label, "Membaca kondisi frekuensi awal router...", "info")
        konfigurasi = ssh_execute_command(
            f"interface wireless print detail where name={interface}", cfg)
        if konfigurasi is None:
            self.log(label, "Gagal koneksi SSH!", "warning")
            return
        self.log(label, "Koneksi SSH berhasil.", "info")
        self.log(label, "Memindai frekuensi tersedia (tunggu ±5 detik)...", "info")
        hasil_scan = ssh_execute_command(
            f"interface wireless scan {interface} duration=5", cfg)
        if not hasil_scan or hasil_scan.strip() == "":
            self.log(label, "Tidak ada hasil scan. Optimasi dihentikan.", "warning")
            return

        freq_sblm, snr_sblm, rssi_sblm, noise_sblm = parse_kondisi_awal(
            konfigurasi, hasil_scan)

        self.log(label, "Mengukur kualitas jaringan SEBELUM optimasi...", "info")
        latency_sblm, loss_sblm = ukur_latency_packetloss(host)  
        tx_sblm, rx_sblm        = ukur_throughput(cfg, interface)

        self.log(label, "─" * 48, "info")
        self.log(label, "KONDISI SEBELUM OPTIMASI:", "info_bold")
        self.log(label, f"  Frekuensi   : {fmt(freq_sblm,  ' MHz')}", "info")
        self.log(label, f"  SNR         : {fmt(snr_sblm,   ' dB')}",  "info")
        self.log(label, f"  RSSI        : {fmt(rssi_sblm,  ' dBm')}", "info")
        self.log(label, f"  Noise Floor : {fmt(noise_sblm, ' dBm')}", "info")
        self.log(label, f"  Latency     : {fmt(latency_sblm, ' ms')}", "info")
        self.log(label, f"  Packet Loss : {fmt(loss_sblm,  '%')}",    "info")
        self.log(label, f"  TX Rate     : {fmt(tx_sblm,    ' Mbps')}", "info")
        self.log(label, f"  RX Rate     : {fmt(rx_sblm,    ' Mbps')}", "info")
        self.log(label, "─" * 48, "info")

        self.log(label, "Hasil Scan Frekuensi:", "info")

        best_data    = None
        best_line    = ""
        parsed_lines = []
        for line in hasil_scan.split("\n"):
            if line.strip() == "":
                continue
            data    = parse_snr_from_line(line)
            snr_val = data["snr"] if data else None
            parsed_lines.append((line, snr_val))

            if data is None:
                continue

            if best_data is None:
                best_data = data
                best_line = line
                continue

            # Rule 1: Pilih frekuensi dengan SNR tertinggi (parameter utama)
            if data["snr"] > best_data["snr"]:
                best_data = data
                best_line = line
            # Rule 2: Jika SNR sama, pilih RSSI (Signal Strength) tertinggi
            elif data["snr"] == best_data["snr"]:
                if data["RSSI"] > best_data["RSSI"]:
                    best_data = data
                    best_line = line
                # Rule 3: Jika SNR dan RSSI sama, pilih Noise Floor terendah
                elif data["RSSI"] == best_data["RSSI"]:
                    if data["noise"] < best_data["noise"]:
                        best_data = data
                        best_line = line

        # Menampilkan seluruh hasil scan, tandai frekuensi optimal
        for line, snr_val in parsed_lines:
            if line == best_line and best_data is not None:
                self.log(label,
                         f"★ {line.strip()}  ← FREKUENSI OPTIMAL"
                         f" (SNR: {best_data['snr']} dB |"
                         f" RSSI: {best_data['RSSI']} dBm |"
                         f" Noise: {best_data['noise']} dBm)",
                         "optimal")
            else:
                self.log(label, line.rstrip(), "success")

        # Menghentikan proses jika frekuensi optimal tidak ditemukan
        if best_data is None:
            self.log(label, "Gagal menentukan frekuensi optimal.", "warning")
            return

        # Menampilkan frekuensi optimal yang akan diterapkan
        self.log(label, "─" * 48, "info")
        self.log(label, "FREKUENSI OPTIMAL DITEMUKAN:", "info_bold")
        self.log(label, f"  Frekuensi : {best_data['freq']} MHz",                           "info_bold")
        self.log(label, f"  SNR       : {best_data['snr']} dB   ← Rule 1: SNR Tertinggi",  "info_bold")
        self.log(label, f"  RSSI      : {best_data['RSSI']} dBm ← Rule 2: RSSI Tertinggi", "info_bold")
        self.log(label, f"  Noise     : {best_data['noise']} dBm ← Rule 3: Noise Terendah","info_bold")
        self.log(label, "─" * 48, "info")

        # Rule 4: Menerapkan frekuensi optimal ke router
        self.log(label, f"Menerapkan frekuensi {best_data['freq']} MHz ke {interface}...", "info")
        hasil_ganti = ssh_execute_command(
            f"interface wireless set {interface} frequency={best_data['freq']}", cfg)
        if hasil_ganti is None:
            self.log(label, "Gagal menerapkan frekuensi!", "warning")
            return
        self.log(label, f"Frekuensi berhasil diubah ke {best_data['freq']} MHz!", "info_bold")

        # Verifikasi frekuensi baru sudah aktif di router
        self.log(label, "Memverifikasi perubahan frekuensi...", "info")
        konfigurasi_akhir = ssh_execute_command(
            f"interface wireless print detail where name={interface}", cfg)
        if konfigurasi_akhir and best_data["freq"] in konfigurasi_akhir:
            self.log(label,
                     f"✔ Verifikasi berhasil: frekuensi {best_data['freq']} MHz aktif.",
                     "success")
        elif konfigurasi_akhir:
            self.log(label, "⚠ Peringatan: frekuensi tidak sesuai setelah diterapkan.", "warning")
        else:
            self.log(label, "Gagal membaca konfigurasi akhir.", "warning")

        # Jeda singkat agar router stabil setelah pergantian frekuensi
        self.log(label, "Menunggu router stabil (3 detik)...", "info")
        time.sleep(3)

        # PENGUKURAN KUALITAS JARINGAN SESUDAH OPTIMASI
        self.log(label, "Mengukur kualitas jaringan SESUDAH optimasi...", "info")
        latency_sdh, loss_sdh = ukur_latency_packetloss(host) 
        tx_sdh, rx_sdh        = ukur_throughput(cfg, interface)

        self.log(label, f"  Latency     : {fmt(latency_sdh, ' ms')}",  "info")
        self.log(label, f"  Packet Loss : {fmt(loss_sdh,    '%')}",    "info")
        self.log(label, f"  TX Rate     : {fmt(tx_sdh,      ' Mbps')}", "info")
        self.log(label, f"  RX Rate     : {fmt(rx_sdh,      ' Mbps')}", "info")

        # PERBANDINGAN SEBELUM VS SESUDAH
        LBL_W = 12          
        VAL_W = 11          
        ARROW = "\u2192"    

        def baris_ringkasan(nama, val_sblm_str, val_sdh_str, keterangan):
            return (f"  {nama:<{LBL_W}}: {val_sblm_str:>{VAL_W}}"
                    f"  {ARROW}  {val_sdh_str:<{VAL_W}}  {keterangan}")

        self.log(label, "═" * 48, "header")
        self.log(label, f"RINGKASAN HASIL OPTIMASI — {label}", "header")
        self.log(label, "═" * 48, "header")

        # Perbandingan Frekuensi
        freq_ket = ("BERUBAH \u2714" if freq_sblm and freq_sblm != best_data["freq"]
                    else "SUDAH OPTIMAL")
        self.log(label,
                 baris_ringkasan("Frekuensi", fmt(freq_sblm, " MHz"),
                                 f"{best_data['freq']} MHz", freq_ket),
                 "info_bold")

        # Perbandingan SNR
        if snr_sblm is not None:
            delta = best_data["snr"] - snr_sblm
            arah  = f"(+{delta} dB \u2191 membaik)" if delta > 0 else \
                    f"({delta} dB \u2193 menurun)"  if delta < 0 else "(tidak berubah)"
            self.log(label,
                     baris_ringkasan("SNR", f"{snr_sblm} dB",
                                     f"{best_data['snr']} dB", arah),
                     "info_bold")
        else:
            self.log(label,
                     baris_ringkasan("SNR", "N/A", f"{best_data['snr']} dB", ""),
                     "info_bold")

        # Perbandingan RSSI
        if rssi_sblm is not None:
            delta = best_data["RSSI"] - rssi_sblm
            arah  = f"(+{delta} dBm \u2191 membaik)" if delta > 0 else \
                    f"({delta} dBm \u2193 menurun)"  if delta < 0 else "(tidak berubah)"
            self.log(label,
                     baris_ringkasan("RSSI", f"{rssi_sblm} dBm",
                                     f"{best_data['RSSI']} dBm", arah),
                     "info_bold")
        else:
            self.log(label,
                     baris_ringkasan("RSSI", "N/A", f"{best_data['RSSI']} dBm", ""),
                     "info_bold")

        # Perbandingan Noise Floor
        if noise_sblm is not None:
            delta = noise_sblm - best_data["noise"]
            arah  = f"({delta} dBm \u2193 membaik)"        if delta > 0 else \
                    f"(+{abs(delta)} dBm \u2191 meningkat)" if delta < 0 else "(tidak berubah)"
            self.log(label,
                     baris_ringkasan("Noise Floor", f"{noise_sblm} dBm",
                                     f"{best_data['noise']} dBm", arah),
                     "info_bold")
        else:
            self.log(label,
                     baris_ringkasan("Noise Floor", "N/A",
                                     f"{best_data['noise']} dBm", ""),
                     "info_bold")

        # Perbandingan Latency (semakin kecil semakin baik)
        if latency_sblm is not None and latency_sdh is not None:
            delta = latency_sblm - latency_sdh
            arah  = f"(\u2193 {delta:.1f} ms membaik)"       if delta > 0 else \
                    f"(\u2191 {abs(delta):.1f} ms meningkat)" if delta < 0 else "(tidak berubah)"
            self.log(label,
                     baris_ringkasan("Latency", f"{latency_sblm:.1f} ms",
                                     f"{latency_sdh:.1f} ms", arah),
                     "info_bold")
        else:
            self.log(label,
                     baris_ringkasan("Latency", fmt(latency_sblm, " ms"),
                                     fmt(latency_sdh, " ms"), ""),
                     "info_bold")

        # Perbandingan Packet Loss (semakin kecil semakin baik)
        if loss_sblm is not None and loss_sdh is not None:
            delta = loss_sblm - loss_sdh
            arah  = f"(\u2193 {delta:.0f}% membaik)"       if delta > 0 else \
                    f"(\u2191 {abs(delta):.0f}% meningkat)" if delta < 0 else "(tidak berubah)"
            self.log(label,
                     baris_ringkasan("Packet Loss", f"{loss_sblm:.0f}%",
                                     f"{loss_sdh:.0f}%", arah),
                     "info_bold")
        else:
            self.log(label,
                     baris_ringkasan("Packet Loss", fmt(loss_sblm, "%"),
                                     fmt(loss_sdh, "%"), ""),
                     "info_bold")

        # Perbandingan TX Rate (semakin besar semakin baik)
        if tx_sblm is not None and tx_sdh is not None:
            delta = tx_sdh - tx_sblm
            arah  = f"(\u2191 {delta:.2f} Mbps membaik)"     if delta > 0 else \
                    f"(\u2193 {abs(delta):.2f} Mbps menurun)" if delta < 0 else "(tidak berubah)"
            self.log(label,
                     baris_ringkasan("TX Rate", f"{tx_sblm:.2f} Mbps",
                                     f"{tx_sdh:.2f} Mbps", arah),
                     "info_bold")
        else:
            self.log(label,
                     baris_ringkasan("TX Rate", fmt(tx_sblm, " Mbps"),
                                     fmt(tx_sdh, " Mbps"), ""),
                     "info_bold")

        # Perbandingan RX Rate (semakin besar semakin baik)
        if rx_sblm is not None and rx_sdh is not None:
            delta = rx_sdh - rx_sblm
            arah  = f"(\u2191 {delta:.2f} Mbps membaik)"     if delta > 0 else \
                    f"(\u2193 {abs(delta):.2f} Mbps menurun)" if delta < 0 else "(tidak berubah)"
            self.log(label,
                     baris_ringkasan("RX Rate", f"{rx_sblm:.2f} Mbps",
                                     f"{rx_sdh:.2f} Mbps", arah),
                     "info_bold")
        else:
            self.log(label,
                     baris_ringkasan("RX Rate", fmt(rx_sblm, " Mbps"),
                                     fmt(rx_sdh, " Mbps"), ""),
                     "info_bold")

        self.log(label, "═" * 48, "header")

        durasi = round(time.time() - waktu_mulai, 2)
        self.log(label, f"Waktu eksekusi: {durasi} detik", "info")

        # Rule 5: Menyimpan seluruh hasil ke database (Tabel 3.2)
        simpan_ke_database(
            router_label=label, jenis_ip=jenis,
            frekuensi=int(best_data["freq"]), snr=best_data["snr"],
            signal=best_data["RSSI"], noise=best_data["noise"],
            action="Ganti Frekuensi", metode="Otomatis", durasi=durasi,
            freq_sblm=freq_sblm,     snr_sblm=snr_sblm,
            rssi_sblm=rssi_sblm,     noise_sblm=noise_sblm,
            latency_sblm=latency_sblm, loss_sblm=loss_sblm,
            tx_sblm=tx_sblm,         rx_sblm=rx_sblm,
            latency_sdh=latency_sdh, loss_sdh=loss_sdh,
            tx_sdh=tx_sdh,           rx_sdh=rx_sdh
        )
        self.log(label, "Data berhasil disimpan ke database.", "info_bold")
        self.log(label, "=" * 50, "header")
        self.log(label, f"OPTIMASI {label.upper()} SELESAI", "header")
        self.log(label, "=" * 50, "header")

    # FUNGSI MENJALANKAN PROSES SECARA SIMULTAN
    def _run_simultan(self, configs, task_func, label_status, on_done=None):
        if not configs:
            messagebox.showwarning("Tidak Ada Router",
                "Tidak ada router yang tersedia / dipilih.")
            return
        
        # Menjalankan seluruh proses secara paralel
        def run_all():
            max_w = min(len(configs), 8)
            with ThreadPoolExecutor(max_workers=max_w) as ex: 
                futures = {ex.submit(task_func, cfg): cfg for cfg in configs}
                for f in as_completed(futures):
                    pass
            self.set_status(f"{label_status} selesai.")
            if on_done:
                on_done()

        self.set_status(f"{label_status} dimulai (simultan)...")
        threading.Thread(target=run_all, daemon=True).start()   

    # FUNGSI MENJALANKAN OPTIMASI SATU ROUTER
    def _run_single_optimasi(self, cfg):
        self.set_status(f"Optimasi {cfg['label']} dimulai...") 
        self._task_optimasi(cfg)                              
        self.set_status(f"Optimasi {cfg['label']} selesai.")  

    # FUNGSI SCAN SEMUA ROUTER IP PUBLIC
    def op_scan_semua_public(self):
        configs = [c for c in self.router_configs if c["jenis"] == "public"] 
        self._switch_log_tab("public")                                      
        self._run_simultan(configs, self._task_scan, "Scan semua IP Public")

    # FUNGSI OPTIMASI SEMUA ROUTER IP PUBLIC
    def op_optimasi_semua_public(self):
        configs = [c for c in self.router_configs if c["jenis"] == "public"] 
        self._switch_log_tab("public")                                       
        self._run_simultan(configs, self._task_optimasi,
                           "Optimasi semua IP Public")

    # FUNGSI SCAN SEMUA ROUTER IP LOCAL
    def op_scan_semua_local(self):
        configs = [c for c in self.router_configs if c["jenis"] == "local"] 
        self._switch_log_tab("local")                                       
        self._run_simultan(configs, self._task_scan, "Scan semua IP Local") 

    # FUNGSI OPTIMASI SEMUA ROUTER IP LOCAL
    def op_optimasi_semua_local(self):
        configs = [c for c in self.router_configs if c["jenis"] == "local"] 
        self._switch_log_tab("local")                                       
        self._run_simultan(configs, self._task_optimasi,
                           "Optimasi semua IP Local")

    # FUNGSI SCAN ROUTER IP PUBLIC TERPILIH
    def op_scan_terpilih_public(self):
        configs = self._get_checked_configs("public") 
        if not configs:
            messagebox.showwarning("Tidak Ada Pilihan",
                "Centang kolom ✓ pada minimal satu router IP Public.")
            return
        self._switch_log_tab("public")
        labels = ", ".join(c["label"] for c in configs)
        self._run_simultan(configs, self._task_scan,
                           f"Scan IP Public terpilih ({labels})")

    # FUNGSI OPTIMASI ROUTER IP PUBLIC TERPILIH
    def op_optimasi_terpilih_public(self):
        configs = self._get_checked_configs("public")
        if not configs:
            messagebox.showwarning("Tidak Ada Pilihan",
                "Centang kolom ✓ pada minimal satu router IP Public.")
            return
        self._switch_log_tab("public")
        labels = ", ".join(c["label"] for c in configs) 
        self._run_simultan(configs, self._task_optimasi,
                           f"Optimasi IP Public terpilih ({labels})")

    # FUNGSI SCAN ROUTER IP LOCAL TERPILIH
    def op_scan_terpilih_local(self):
        configs = self._get_checked_configs("local")
        if not configs:
            messagebox.showwarning("Tidak Ada Pilihan",
                "Centang kolom ✓ pada minimal satu router IP Local.")
            return
        self._switch_log_tab("local")
        labels = ", ".join(c["label"] for c in configs) 
        self._run_simultan(configs, self._task_scan,
                           f"Scan IP Local terpilih ({labels})")

    # FUNGSI OPTIMASI ROUTER IP LOCAL TERPILIH
    def op_optimasi_terpilih_local(self):
        configs = self._get_checked_configs("local")
        if not configs:
            messagebox.showwarning("Tidak Ada Pilihan",
                "Centang kolom ✓ pada minimal satu router IP Local.")
            return
        self._switch_log_tab("local")
        labels = ", ".join(c["label"] for c in configs)
        self._run_simultan(configs, self._task_optimasi,
                           f"Optimasi IP Local terpilih ({labels})")

# PROGRAM UTAMA (ENTRY POINT)
if __name__ == "__main__":
    app = AplikasiOptimasiJenny()
    app.mainloop()