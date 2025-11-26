
# -*- coding: utf-8 -*-
import os
import shutil
from datetime import datetime
import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# =========================
# 可配置参数
# =========================
UNIQUE_KEYS = ["SHIPMENT ID"]                   # 去重唯一键（如需复合键：["SHIPMENT ID","ORDER INSERT DATE"]）
OLD_TIME_COL = "ORDER INSERT DATE"             # 旧数据用于增量判断的时间列
NEW_TIME_COL = "SHIPMENT CREATION DATE"        # 新数据用于增量判断的时间列
TIME_COLS_NEW = [                              # 新数据里需要强制转换的所有时间列（按你的列名）
    "SHIPMENT CREATION DATE",
    "SHIPMENT GATE OUT SOURCE",
    "SHIPMENT GATE IN SOURCE",
    "SHIPMENT GATE IN DESTINATION",
    "SHIPMENT GATE OUT DESTINATION",
]
INPUT_DT_FORMAT = "%Y/%m/%d %H:%M:%S"          # 新数据主解析格式（例如 2025/07/10 15:42:00）
OUTPUT_DT_FORMAT = "%m/%d/%Y %I:%M:%S %p"      # 导出显示格式 mm/dd/yyyy hh:mm:ss AM/PM
ENABLE_BACKUP_OLD = True                       # 是否为旧 CSV 做时间戳备份（避免误操作）

# =========================
# 工具函数
# =========================
def log(text_widget: ScrolledText, msg: str):
    text_widget.insert(tk.END, msg + "\n")
    text_widget.see(tk.END)
    text_widget.update()

def ensure_backup(old_csv_path: str, log_widget: ScrolledText):
    """为旧 CSV 在同目录生成时间戳备份。"""
    if ENABLE_BACKUP_OLD and old_csv_path and os.path.exists(old_csv_path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dirname = os.path.dirname(old_csv_path)
        base = os.path.basename(old_csv_path)
        backup_name = f"{ts}_{base}"
        backup_path = os.path.join(dirname, backup_name)
        shutil.copy2(old_csv_path, backup_path)
        log(log_widget, f"[备份] 已备份旧文件到：{backup_path}")
    else:
        log(log_widget, "[备份] 未开启备份或未找到旧文件，跳过备份")

def parse_datetime_force(series: pd.Series,
                         primary_fmt: str = None,
                         excel_epoch: str = "1900",
                         dayfirst: bool = False) -> pd.Series:
    """
    强制把任意列解析为 datetime（尽量不留 NaT）：
    1) 按 primary_fmt 严格解析
    2) 失败值用 pandas 自动解析
    3) 对仍失败且是纯数字的值按 Excel 序列日期转换（1900/1904）
    返回 dtype=datetime64[ns] 的 Series
    """
    s = series.copy()

    # 已经是 datetime，直接返回
    if pd.api.types.is_datetime64_any_dtype(s):
        return s

    # 统一为字符串并去除首尾空白
    s_str = s.astype(str).str.strip()

    # Step 1：主格式解析
    if primary_fmt:
        dt = pd.to_datetime(s_str, format=primary_fmt, errors="coerce")
    else:
        dt = pd.to_datetime(s_str, errors="coerce", dayfirst=dayfirst, infer_datetime_format=True)

    # Step 2：自动解析填充 NaT
    mask_nat = dt.isna()
    if mask_nat.any():
        dt_auto = pd.to_datetime(s_str[mask_nat], errors="coerce", dayfirst=dayfirst, infer_datetime_format=True)
        dt.loc[mask_nat] = dt_auto

    # Step 3：Excel 序列日期（纯数字）
    mask_nat = dt.isna()
    if mask_nat.any():
        s_num = pd.to_numeric(s_str[mask_nat], errors="coerce")
        mask_num = s_num.notna()
        if mask_num.any():
            origin = "1899-12-30" if excel_epoch == "1900" else "1904-01-01"
            dt_num = pd.to_datetime(s_num[mask_num], unit="D", origin=origin, errors="coerce")
            dt.loc[s_num.index[mask_num]] = dt_num

    return dt

def format_datetime_cols_for_export(df: pd.DataFrame, cols, fmt: str):
    """
    导出前将指定列格式化为字符串显示格式；即使是字符串/NaT也尽量解析后再格式化。
    """
    for c in cols:
        if c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                df[c] = df[c].dt.strftime(fmt)
            else:
                parsed = pd.to_datetime(df[c], errors="coerce", infer_datetime_format=True)
                df[c] = parsed.dt.strftime(fmt)
    return df

def align_columns_union(df_a: pd.DataFrame, df_b: pd.DataFrame, mapping_b_to_a=None):
    """
    对齐列名：必要时把 b 的列改名为 a 的列名；用并集列 reindex，确保 concat 对齐。
    """
    if mapping_b_to_a:
        df_b = df_b.rename(columns=mapping_b_to_a)
    all_cols = list(set(df_a.columns) | set(df_b.columns))
    df_a = df_a.reindex(columns=all_cols)
    df_b = df_b.reindex(columns=all_cols)
    return df_a, df_b

# =========================
# 主处理逻辑
# =========================
def run_merge(old_csv_path: str, new_xlsx_path: str, log_widget: ScrolledText):
    try:
        # 校验旧 CSV 路径（因为要覆盖写入）
        if not old_csv_path:
            raise FileNotFoundError("未选择旧数据 CSV。当前要求将结果保存到旧数据路径，必须提供旧 CSV。")
        if not os.path.exists(old_csv_path):
            raise FileNotFoundError(f"未找到旧数据 CSV 文件：{old_csv_path}")

        # 1) 备份旧 CSV
        ensure_backup(old_csv_path, log_widget)

        # 2) 读取旧 CSV（先按字符串）
        df_a = pd.read_csv(old_csv_path, dtype=str)
        log(log_widget, f"[读取] 旧数据：{old_csv_path}，行数 {len(df_a)}")

        # 3) 读取新 XLSX（指定 openpyxl 引擎）
        if not new_xlsx_path or not os.path.exists(new_xlsx_path):
            raise FileNotFoundError("未选择或找不到新数据 XLSX 文件。")
        df_b = pd.read_excel(new_xlsx_path, dtype=str, engine="openpyxl")
        log(log_widget, f"[读取] 新数据：{new_xlsx_path}，行数 {len(df_b)}")

        # 4) 强制转换时间列
        # 旧数据时间列（用于增量判断）
        if OLD_TIME_COL in df_a.columns and len(df_a) > 0:
            df_a[OLD_TIME_COL] = parse_datetime_force(df_a[OLD_TIME_COL], primary_fmt=None)
        else:
            log(log_widget, f"[警告] 旧数据缺少时间列 '{OLD_TIME_COL}' 或旧数据为空，增量判断将视为无历史时间")

        # 新数据所有时间列
        for col in TIME_COLS_NEW:
            if col in df_b.columns:
                df_b[col] = parse_datetime_force(df_b[col], primary_fmt=INPUT_DT_FORMAT)
            else:
                log(log_widget, f"[提示] 新数据缺少时间列 '{col}'，跳过该列的转换")

        # 增量判断列必须存在
        if NEW_TIME_COL not in df_b.columns:
            raise ValueError(f"新数据缺少时间列 '{NEW_TIME_COL}'，请检查列名。")

        # 5) 增量过滤
        if OLD_TIME_COL in df_a.columns and pd.api.types.is_datetime64_any_dtype(df_a[OLD_TIME_COL]):
            max_old_dt = df_a[OLD_TIME_COL].max()
            if pd.notna(max_old_dt):
                df_b_incremental = df_b[df_b[NEW_TIME_COL] > max_old_dt].copy()
                log(log_widget, f"[增量] 旧数据最大时间：{max_old_dt}；新数据总行：{len(df_b)}；增量行：{len(df_b_incremental)}")
            else:
                df_b_incremental = df_b.copy()
                log(log_widget, "[增量] 旧数据最大时间为 NaT，保留新数据全部行")
        else:
            df_b_incremental = df_b.copy()
            log(log_widget, "[增量] 旧数据时间列不存在或不是 datetime，保留新数据全部行")

        # 6) 列对齐（如需映射可在此配置）
        mapping_b_to_a = {
            # 例如： "Shipment ID": "SHIPMENT ID",
            #       "Order Insert Date": "ORDER INSERT DATE",
        }
        df_a_aligned, df_b_aligned = align_columns_union(df_a, df_b_incremental, mapping_b_to_a)

        # 7) 合并（旧在前，新在后 -> 保留旧数据优先）
        combined = pd.concat([df_a_aligned, df_b_aligned], ignore_index=True)

        # 8) 去重（按唯一键）
        missing_keys = [k for k in UNIQUE_KEYS if k not in combined.columns]
        if missing_keys:
            raise ValueError(f"去重唯一键缺失：{missing_keys}；请检查列名或映射。")
        result = combined.drop_duplicates(subset=UNIQUE_KEYS, keep="first")
        log(log_widget, f"[去重] 合并前行数：{len(combined)}；去重后行数：{len(result)}")

        # 9) 导出前时间格式化（统一显示）
        time_cols_to_format = list(set([OLD_TIME_COL, NEW_TIME_COL] + TIME_COLS_NEW))
        result = format_datetime_cols_for_export(result, time_cols_to_format, OUTPUT_DT_FORMAT)

        # 10) 保存结果（直接覆盖到旧 CSV 路径）
        out_path = old_csv_path  # 覆盖旧文件。如果要改为同目录的另一个文件名，改成下面一行：
        # out_path = os.path.join(os.path.dirname(old_csv_path), "source data.csv")

        result.to_csv(out_path, index=False, encoding="utf-8-sig")
        log(log_widget, f"[完成] 已保存合并结果到（覆盖旧文件）：{out_path}")
        messagebox.showinfo("完成", f"合并完成，结果已覆盖保存到：\n{out_path}")

    except Exception as e:
        log(log_widget, f"[错误] {str(e)}")
        messagebox.showerror("错误", f"处理失败：\n{str(e)}")

# =========================
# 图形界面
# =========================
def main():
    root = tk.Tk()
    root.title("数据合并工具（旧CSV + 新XLSX，增量 & 去重）")
    root.geometry("700x500")

    old_path_var = tk.StringVar()
    new_path_var = tk.StringVar()

    # 文件选择区
    frame_top = tk.Frame(root)
    frame_top.pack(fill=tk.X, padx=10, pady=10)

    tk.Label(frame_top, text="旧数据 CSV：").grid(row=0, column=0, sticky="w")
    tk.Entry(frame_top, textvariable=old_path_var, width=60).grid(row=0, column=1, padx=5)
    tk.Button(frame_top, text="选择...", command=lambda: old_path_var.set(
        filedialog.askopenfilename(title="选择旧数据 CSV", filetypes=[("CSV 文件", "*.csv")])
    )).grid(row=0, column=2, padx=5)

    tk.Label(frame_top, text="新数据 XLSX：").grid(row=1, column=0, sticky="w", pady=(8,0))
    tk.Entry(frame_top, textvariable=new_path_var, width=60).grid(row=1, column=1, padx=5, pady=(8,0))
    tk.Button(frame_top, text="选择...", command=lambda: new_path_var.set(
        filedialog.askopenfilename(title="选择新数据 XLSX", filetypes=[("Excel 文件", "*.xlsx")])
    )).grid(row=1, column=2, padx=5, pady=(8,0))

    # 操作按钮与日志
    frame_mid = tk.Frame(root)
    frame_mid.pack(fill=tk.X, padx=10, pady=5)
    log_box = ScrolledText(root, height=18)
    log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    def on_run():
        old_path = old_path_var.get().strip()
        new_path = new_path_var.get().strip()
        if not old_path:
            messagebox.showwarning("提示", "必须选择旧数据 CSV（因为结果将覆盖保存到该路径）。")
            return
        if not new_path:
            messagebox.showwarning("提示", "请先选择新数据 XLSX 文件。")
            return
        log(log_box, "====== 开始处理 ======")
        run_merge(old_path, new_path, log_box)
        log(log_box, "====== 处理结束 ======")

    tk.Button(frame_mid, text="运行合并并覆盖保存", command=on_run, width=20).pack(side=tk.LEFT, padx=5)

    root.mainloop()

if __name__ == "__main__":
    main()
