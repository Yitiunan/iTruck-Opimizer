import pandas as pd
import os
import numpy as np
from modules.callout_trip_concat import callout_trip_concat
from modules.callout_to_rental import simulate_callout_to_rental
from modules.fee_model import calculate_special_lane_cost
from modules.rental_to_callout import rental_to_callout
from modules.rental_calculation import calculate_rental_time_utilization
import re

def perform_rental_reduction(processed_data_path,
                             main_lane_forecast_path,
                             rental_truck_list_path,
                             exchange_rate_path,
                             callout_cost_path,
                             special_lane_cost_path,
                             equipment_vehicle_type_path,
                             output_path_callout_cost,
                             rental_cost,
                             output_path_special_lane_cost,
                             rental_to_callout_compare_path,  # 新增参数，compare_df保存路径
                             rental_time_util_output_path,
                             Predict_output_dir):
    def get_quarter(date):
        if pd.isna(date):
            return None
        if date.month in [1, 2, 3]:
            return 'Q1'
        elif date.month in [4, 5, 6]:
            return 'Q2'
        elif date.month in [7, 8, 9]:
            return 'Q3'
        elif date.month in [10, 11, 12]:
            return 'Q4'
        return None

    def months_in_year_quarter(yq: pd.Period):
        start = yq.start_time
        return [start.month,
                (start + pd.offsets.MonthBegin(1)).month,
                (start + pd.offsets.MonthBegin(2)).month]

    def month_bounds(year: int, month: int):
        month_start = pd.Timestamp(year=year, month=month, day=1, hour=0, minute=0, second=0)
        month_end = (month_start + pd.offsets.MonthEnd(1)).replace(hour=23, minute=0, second=0)
        return month_start, month_end

    def allocate_to_months(total_count: int, n_months: int = 3) -> list:
        base = total_count // n_months
        r = total_count % n_months
        return [base + (1 if i < r else 0) for i in range(n_months)]

    rng = np.random.default_rng(2024)
    df_reduced = pd.read_csv(processed_data_path)
    main_lane_forecast = pd.read_csv(main_lane_forecast_path)
    rental_truck_list = pd.read_csv(rental_truck_list_path)

    # 转换时间列并计算季度
    df_reduced['SHIPMENT CREATION DATE'] = pd.to_datetime(df_reduced['SHIPMENT CREATION DATE'])
    df_reduced['Quarter'] = df_reduced['SHIPMENT CREATION DATE'].apply(get_quarter)
    df_reduced['YearQuarter'] = df_reduced['SHIPMENT CREATION DATE'].dt.to_period('Q')
    df_reduced['Truck Weight'] = None
    # 1. 获取普通货物车的车牌列表
    general_cargo_truck_plates = rental_truck_list.loc[
        rental_truck_list['Cargo Type'] == 'General Cargo', 'Truck Plate'
    ].tolist()

    # 2. 过滤 df_reduced，保留 Callout 全部 和 Rental 且车牌属于普通货物车牌的行
    df_reduced = df_reduced.loc[
        (df_reduced['Transport Mode'] == 'Callout') |
        ((df_reduced['Transport Mode'] == 'Rental') & (df_reduced['Truck Plate'].isin(general_cargo_truck_plates)))
        ].copy()

    # 确保时间列为 datetime 类型（对缺失值安全）
    for col in ['SHIPMENT GATE IN SOURCE', 'SHIPMENT GATE OUT DESTINATION', 'SHIPMENT CREATION DATE']:
        if col in df_reduced.columns:
            df_reduced[col] = pd.to_datetime(df_reduced[col], errors='coerce')
    # 2) Rental：从租赁清单 Vehicle Type 提取（按 Truck Plate 映射）
    if 'Truck Plate' in df_reduced.columns and 'Truck Plate' in rental_truck_list.columns and 'Vehicle Type' in rental_truck_list.columns:
        rental_truck_list['WeightFromType'] = rental_truck_list['Vehicle Type'].astype(str).str.extract(r'(\d+T)')[0]
        plate_to_weight = (rental_truck_list.dropna(subset=['WeightFromType'])
        .drop_duplicates(subset=['Truck Plate'])
        .set_index('Truck Plate')['WeightFromType'])
        mask_rental = df_reduced['Transport Mode'].astype(str).str.strip().eq('Rental')
        df_reduced.loc[mask_rental, 'Truck Weight'] = df_reduced.loc[mask_rental, 'Truck Plate'].map(plate_to_weight)

    # 这里先不过滤完整季度，避免数据量变小导致无匹配
    df_reduced_complete = df_reduced.copy()

    # 在调用主循环之前，新增一个列表用于存放reduce_percentage记录
    reduce_pct_records = []

    df_reduced_complete = df_reduced.copy()
    df_reduced_complete['orig_index'] = df_reduced_complete.index

    indices_to_drop = set()
    indices_dropped = set()

    for idx_row, row in main_lane_forecast.iterrows():
        fluctuation = str(row.get('fluctuation', '')).lower()
        if fluctuation != 'reduce':
            continue
        lane = row.get('Lane')
        quarter_str = str(row.get('Quarter', '')).upper()
        lane_distance = row.get('Lane Distance', np.nan)

        if pd.isna(lane) or not quarter_str.startswith('Q'):
            print(f"跳过无效路径或季度，lane={lane}, quarter={quarter_str}")
            continue

        lane_df = df_reduced_complete[df_reduced_complete['SHIPMENT LANE'] == lane]
        if lane_df.empty:
            print(f"路径 {lane} 在数据中无匹配行程")
            continue

        try:
            q_num = int(quarter_str.replace('Q', ''))
        except Exception:
            print(f"季度转换失败，quarter_str={quarter_str}")
            continue

        year_quarters = sorted([p for p in lane_df['YearQuarter'].dropna().unique()
                                if getattr(p, 'quarter', None) == q_num])
        if not year_quarters:
            print(f"路径 {lane} 无匹配季度 {quarter_str} 数据")
            continue

        for yq in year_quarters:
            for weight in ['5T', '10T', '15T', '20T']:
                reduce_percentage_raw = row.get(weight, '0%')
                if isinstance(reduce_percentage_raw, str) and reduce_percentage_raw.endswith('%'):
                    try:
                        reduce_percentage = float(reduce_percentage_raw.strip('%')) / 100
                    except ValueError:
                        reduce_percentage = 0.0
                else:
                    try:
                        reduce_percentage = float(reduce_percentage_raw)
                    except (ValueError, TypeError):
                        reduce_percentage = 0.0

                print(
                    f"路径 {lane}，季度 {yq}，重量 {weight}，减少比例原始值 {reduce_percentage_raw}，处理后 {reduce_percentage}")

                if pd.isna(reduce_percentage) or reduce_percentage >= 0:
                    print(f"路径 {lane}，季度 {yq}，重量 {weight}，减少比例 {reduce_percentage} 不合理，跳过")
                    continue

                # 将reduce_percentage的记录保存下来，方便后续合并
                reduce_pct_records.append({
                    'SHIPMENT LANE': lane,
                    'Quarter': yq,  # 注意Quarter格式，后面合并时要统一格式
                    'Truck Weight': weight,
                    'rate': reduce_percentage
                })

                weight_subset = lane_df[
                    (lane_df['YearQuarter'] == yq) &
                    (lane_df['Truck Weight'] == weight) &
                    (lane_df['Transport Mode'] == 'Rental')
                    ]

                total_existing = len(weight_subset)
                if total_existing <= 0:
                    print(f"路径 {lane}，季度 {yq}，重量 {weight} 无符合行程")
                    continue

                reduce_count = max(1, round(abs(total_existing * reduce_percentage)))
                if reduce_count <= 0:
                    print(f"路径 {lane}，季度 {yq}，重量 {weight} 计算减少数量为0")
                    continue

                months = months_in_year_quarter(yq)
                year = yq.start_time.year
                monthly_reduce_counts = allocate_to_months(reduce_count, n_months=3)

                for i, month in enumerate(months):
                    need_reduce = monthly_reduce_counts[i]
                    if need_reduce <= 0:
                        continue

                    month_start, month_end = month_bounds(year, month)
                    month_weight_subset = weight_subset[
                        (weight_subset['SHIPMENT GATE IN SOURCE'] >= month_start) &
                        (weight_subset['SHIPMENT GATE IN SOURCE'] <= month_end)
                        ]

                    if month_weight_subset.empty:
                        print(f"路径 {lane}，{yq} {year}-{month:02d} 无符合时间窗口行程")
                        continue

                    if len(month_weight_subset) < need_reduce:
                        print(f"警告：路径 {lane}，{yq} {year}-{month:02d}，重量{weight}，"
                              f"需要减少 {need_reduce} 条，但仅有 {len(month_weight_subset)} 条，全部减少。")
                        need_reduce = len(month_weight_subset)

                    reduce_indices = rng.choice(month_weight_subset.index, size=need_reduce, replace=False)
                    indices_to_drop.update(reduce_indices)
                    print(f"减少路径={lane}, 季度={yq}, 重量={weight}, 需要减少数={need_reduce}")
                    print("month_weight_subset索引示例：", month_weight_subset.index[:5])
                    print("选择减少的reduce_indices：", reduce_indices)
                    indices_dropped.update(df_reduced_complete.loc[reduce_indices, 'orig_index'].tolist())

    # 生成reduce_percentage的DataFrame
    reduce_pct_df = pd.DataFrame(reduce_pct_records)
    reduce_pct_df['Quarter'] = reduce_pct_df['Quarter'].apply(get_quarter)

    # 原始计算的distance_variance_df
    df_original = df_reduced_complete.copy()  # 原始含orig_index
    df_result = df_reduced_complete.drop(index=list(indices_to_drop)).reset_index(drop=True)

    df_added_shipments = df_original[df_original['orig_index'].isin(indices_dropped)].copy()

    if 'SHIPMENT DISTANCE' not in df_added_shipments.columns:
        raise ValueError("数据中缺少 'SHIPMENT DISTANCE' 列，无法计算distance variance")

    distance_variance_df = (
        df_added_shipments
        .groupby(['SHIPMENT LANE', 'Truck Weight', 'Quarter'], dropna=False)['SHIPMENT DISTANCE']
        .sum()
        .reset_index()
        .rename(columns={'SHIPMENT DISTANCE': 'distance variance'})
    )

    # 合并 Location，保持你原来的代码
    main_lane_forecast = main_lane_forecast.rename(columns={'Lane': 'SHIPMENT LANE'})
    distance_variance_df = distance_variance_df.merge(
        main_lane_forecast[['SHIPMENT LANE', 'Location']],
        on='SHIPMENT LANE',
        how='left'
    )

    distance_variance_df = distance_variance_df.drop_duplicates()
    distance_variance_df['distance variance'] = -distance_variance_df['distance variance'].abs()

    # --- 新增步骤：将 reduce_percentage 合并进 distance_variance_df ---
    # 注意Quarter字段类型，如果是Period，需要先统一格式
    def quarter_to_str(q):
        if pd.api.types.is_period_dtype(q):
            return str(q)
        return str(q)

    distance_variance_df['Quarter'] = distance_variance_df['Quarter'].apply(quarter_to_str)
    reduce_pct_df['Quarter'] = reduce_pct_df['Quarter'].apply(quarter_to_str)

    distance_variance_df = distance_variance_df.merge(
        reduce_pct_df[['SHIPMENT LANE', 'Truck Weight', 'Quarter', 'rate']],
        on=['SHIPMENT LANE', 'Truck Weight', 'Quarter'],
        how='left'
    )

    # 保存结果
    distance_variance_df.to_csv(
        os.path.join(Predict_output_dir, 'distance_variance_output.csv'),
        index=False
    )

    os.makedirs(Predict_output_dir, exist_ok=True)
    output_path = os.path.join(Predict_output_dir, "future_rental_shipments.csv")

    df_result = df_result[df_result['Transport Mode'] == 'Rental']
    df_result.to_csv(output_path, index=False, encoding='utf-8-sig')

    print(f"减少后行程保存成功，共删除 {len(indices_to_drop)} 条行程，结果保存在：{output_path}")
    # 调用 rental_to_callout 计算月租转外叫费用
    compare_df, summary_df = rental_to_callout(
        rental_info_path=output_path,
        rental_truck_list_path=rental_truck_list_path,
        exchange_rate_path=exchange_rate_path,
        callout_cost_path=callout_cost_path,
        special_lane_cost_path=special_lane_cost_path,
        equipment_vehicle_type_path=equipment_vehicle_type_path,
        output_path_callout_cost=output_path_callout_cost,
        rental_cost=rental_cost,
        output_path_special_lane_cost=output_path_special_lane_cost
    )
    # 保存 compare_df 到 rental_to_callout_compare_path
    compare_df.to_csv(rental_to_callout_compare_path, index=False, encoding='utf-8-sig')

    # 2. 调用 calculate_rental_time_utilization，传入compare_df路径
    calculate_rental_time_utilization(
        rental_info_path=output_path,
        output_path=rental_time_util_output_path,
        rental_cost_path=rental_cost,
        callout_cost_path=callout_cost_path,
        rental_truck_list_path=rental_truck_list_path,
        exchange_rate_path=exchange_rate_path,
        rental_to_callout_compare_path=rental_to_callout_compare_path
    )

    # 你可以根据需要返回更多结果
    return df_result, distance_variance_df, compare_df, summary_df

def perform_callout_prediction(
    processed_data_path,
    rental_truck_list_path,
    main_lane_forecast_path,
    callout_cost_path,
    special_lane_cost_path,
    exchange_rate_path,
    transit_time_path,
    rental_cost_path,
    Predict_output_dir
):
    """
    功能：执行 Callout 预测主流程
    输入：
        各路径文件均由 App.py 传入
    输出：
        返回预测结果 DataFrame，并保存结果文件
    """
    print("🚀 开始执行 Callout 预测流程...")

    # ==============================
    # 1. 读取数据
    # ==============================
    df = pd.read_csv(processed_data_path)
    rental_truck_list = pd.read_csv(rental_truck_list_path)
    main_lane_forecast = pd.read_csv(main_lane_forecast_path)
    callout_cost = pd.read_csv(callout_cost_path)
    special_lane_cost = pd.read_csv(special_lane_cost_path, index_col=0)
    exchange_rate = pd.read_excel(exchange_rate_path)
    Main_lane_forecast = pd.read_csv(main_lane_forecast_path)

    def extract_weight(equipment_id):
        """从字符串中提取类似 5T/10T 的重量标记"""
        m = re.search(r'(\d+T)', str(equipment_id))
        return m.group(1) if m else None

    def get_quarter(date):
        """返回 Q1/Q2/Q3/Q4；对 NaT 安全"""
        if pd.isna(date):
            return None
        if date.month in [1, 2, 3]:
            return 'Q1'
        elif date.month in [4, 5, 6]:
            return 'Q2'
        elif date.month in [7, 8, 9]:
            return 'Q3'
        elif date.month in [10, 11, 12]:
            return 'Q4'
        return None

    def is_valid(candidate, existing_list, min_interval_hours: int) -> bool:
        """校验 candidate 与 existing_list（包含已有 + 已新增）是否满足最小间隔（小时）"""
        # 简单 O(N) 校验；如需更高性能可改为邻近二分检索
        for t in existing_list:
            diff_hours = abs((candidate - t).total_seconds()) / 3600.0
            if diff_hours < min_interval_hours:
                return False
        return True

    def months_in_year_quarter(yq: pd.Period):
        """返回该年季度包含的三个月份整数，如 2024Q1 -> [1,2,3]"""
        start = yq.start_time  # 季度首月 1 日 00:00
        return [start.month,
                (start + pd.offsets.MonthBegin(1)).month,
                (start + pd.offsets.MonthBegin(2)).month]

    def month_bounds(year: int, month: int):
        """返回该月的起止时间戳（起：当月1日00:00；止：当月末日23:00）"""
        month_start = pd.Timestamp(year=year, month=month, day=1, hour=0, minute=0, second=0)
        month_end = (month_start + pd.offsets.MonthEnd(1)).replace(hour=23, minute=0, second=0)
        return month_start, month_end

    def allocate_to_months(total_count: int, n_months: int = 3) -> list:
        """把 total_count 尽量均匀分配到 n_months 个月，例如 5 -> [2,2,1]"""
        base = total_count // n_months
        r = total_count % n_months
        return [base + (1 if i < r else 0) for i in range(n_months)]

    def parse_percentage(val):
        if pd.isna(val):
            return 0.0
        if isinstance(val, str):
            val = val.strip()
            if val.endswith('%'):
                return float(val.strip('%')) / 100
            else:
                return float(val)
        return float(val)
    # 固定随机种子（可复现随机分布；如不需要复现，可注释或改成时间种子）
    rng = np.random.default_rng(2024)

    # ======================
    # 预处理与清洗
    # ======================

    # 去除列名多余空格（防止 join 出错）
    df.columns = df.columns.str.strip()
    rental_truck_list.columns = rental_truck_list.columns.str.strip()
    Main_lane_forecast.columns = Main_lane_forecast.columns.str.strip()

    # 确保时间列为 datetime 类型（对缺失值安全）
    for col in ['SHIPMENT GATE IN SOURCE', 'SHIPMENT GATE OUT DESTINATION', 'SHIPMENT CREATION DATE']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    # ---- 安全地转换 Main_lane_forecast 百分比列 ----
    for col in ['5T', '10T', '15T', '20T']:
        if col in Main_lane_forecast.columns:
            # 去除%并转换为小数，NaN 保留为 NaN
            Main_lane_forecast[col] = pd.to_numeric(Main_lane_forecast[col], errors='coerce')
        else:
            # 若列不存在，创建为 0
            Main_lane_forecast[col] = 0.0

    # ---- 清洗 interval time，避免 SettingWithCopy ----
    Main_lane_forecast['interval time'] = pd.to_numeric(Main_lane_forecast['interval time'], errors='coerce')
    Main_lane_forecast['interval time'] = Main_lane_forecast['interval time'].fillna(1)  # 缺失默认1天
    Main_lane_forecast.loc[Main_lane_forecast['interval time'] < 0.0417, 'interval time'] = 0.0417  # 最小约1小时（1/24天）

    # ======================
    # 车辆重量识别（避免逐行 join 的性能问题）
    # ======================
    df['Truck Weight'] = None

    # 1) Callout：直接从 EQUIPMENT ID 提取
    mask_callout = df['Transport Mode'].astype(str).str.strip().eq('Callout')
    if 'EQUIPMENT ID' in df.columns:
        df.loc[mask_callout, 'Truck Weight'] = df.loc[mask_callout, 'EQUIPMENT ID'].apply(extract_weight)

    # 2) Rental：从租赁清单 Vehicle Type 提取（按 Truck Plate 映射）
    if 'Truck Plate' in df.columns and 'Truck Plate' in rental_truck_list.columns and 'Vehicle Type' in rental_truck_list.columns:
        rental_truck_list['WeightFromType'] = rental_truck_list['Vehicle Type'].astype(str).str.extract(r'(\d+T)')[0]
        plate_to_weight = (rental_truck_list.dropna(subset=['WeightFromType'])
        .drop_duplicates(subset=['Truck Plate'])
        .set_index('Truck Plate')['WeightFromType'])
        mask_rental = df['Transport Mode'].astype(str).str.strip().eq('Rental')
        df.loc[mask_rental, 'Truck Weight'] = df.loc[mask_rental, 'Truck Plate'].map(plate_to_weight)

    # ======================
    # 季度与 YearQuarter（跨年不混淆）
    # ======================
    df['Quarter'] = df['SHIPMENT CREATION DATE'].apply(get_quarter)
    df['YearQuarter'] = df['SHIPMENT CREATION DATE'].dt.to_period('Q')  # 例如 2024Q1; NaT -> <NA>

    # 统计并过滤完整季度（这里以“有数据”为准）
    quarter_counts = df['YearQuarter'].value_counts().sort_index()
    complete_quarters = quarter_counts[quarter_counts > 0].index
    df_complete_quarters = df[df['YearQuarter'].isin(complete_quarters)]

    # ======================
    # 根据 Main_lane_forecast 生成新增行程（季度→月均分；月内随机；满足最小间隔）
    # ======================
    result_list = []
    add_pct_records = []

    for _, row in Main_lane_forecast.iterrows():
        lane = row.get('Lane')
        quarter_str = str(row.get('Quarter', '')).upper()  # 形如 'Q1'
        lane_distance = row.get('Lane Distance', np.nan)
        interval_days = row.get('interval time', 1)

        if pd.isna(lane) or not quarter_str.startswith('Q'):
            continue

        # 转小时并确保>=1
        interval_hours = int(round(float(interval_days) * 24))
        interval_hours = max(interval_hours, 1)

        # 该 Lane 的完整季度数据
        lane_df = df_complete_quarters[df_complete_quarters['SHIPMENT LANE'] == lane]

        if lane_df.empty:
            continue

        # 解析 Q编号
        try:
            q_num = int(quarter_str.replace('Q', ''))
        except Exception:
            continue

        # 找出该 Lane 且季度号为 q_num 的 YearQuarter（跨年不串）
        year_quarters = sorted([p for p in lane_df['YearQuarter'].dropna().unique()
                                if getattr(p, 'quarter', None) == q_num])

        for yq in year_quarters:
            # 针对四个重量档分别处理
            for weight in ['5T', '10T', '15T', '20T']:
                add_percentage = parse_percentage(row.get(weight, 0.0))
                if pd.isna(add_percentage) or add_percentage <= 0:
                    continue
                add_pct_records.append({
                    'SHIPMENT LANE': lane,
                    'Quarter': yq,  # 注意：确保yq类型和后续一致(比如字符串或时间段)
                    'Truck Weight': weight,
                    'rate': add_percentage,
                })
                # 转成DataFrame
                df_add_percentage = pd.DataFrame(add_pct_records)
                df_add_percentage['Quarter'] = df_add_percentage['Quarter'].apply(get_quarter)
                # 筛选该 YearQuarter + Lane + Weight 的已存在行程
                weight_subset = lane_df[
                    (lane_df['YearQuarter'] == yq) &
                    (lane_df['Truck Weight'] == weight)
                    ]

                total_existing = len(weight_subset)

                if total_existing <= 0:
                    continue

                # 计算新增量（向下取整）
                add_count = int(total_existing * float(add_percentage))
                if add_count <= 0:
                    continue

                # 平均运输用时（目的地出门 - 始发进门）
                avg_timedelta = (weight_subset['SHIPMENT GATE OUT DESTINATION'] - weight_subset[
                    'SHIPMENT GATE IN SOURCE']).mean()

                # 该 YearQuarter 内所有已存在的发运时间（用于最小间隔校验，覆盖跨月影响）
                existing_times = (
                    weight_subset['SHIPMENT GATE IN SOURCE']
                    .dropna()
                    .sort_values()
                )
                if existing_times.empty or pd.isna(avg_timedelta):
                    # 缺数据无法计算运输时长/起运时间，跳过该组
                    continue

                # --- 关键：季度内按月均匀分配 ---
                months = months_in_year_quarter(yq)  # 例如 [1, 2, 3]
                year = yq.start_time.year
                monthly_counts = allocate_to_months(add_count, n_months=3)

                # 用于全季度的最小间隔约束（包含已有 + 新增）
                existing_list = list(existing_times)
                # 需要从已存在记录中取 LOCATION 与 Special_Lane 作为模板
                location = weight_subset.iloc[0]['LOCATION'] if 'LOCATION' in weight_subset.columns else None
                special_lane_value = weight_subset.iloc[0][
                    'Special_Lane'] if 'Special_Lane' in weight_subset.columns else None

                # ========= 方案 A：随机偏移 + 随机遍历（推荐）=========
                for i, month in enumerate(months):
                    need = monthly_counts[i]
                    if need <= 0:
                        continue

                    month_start, month_end = month_bounds(year, month)

                    collected = []

                    # 月内所有整点小时
                    all_hours = pd.date_range(start=month_start, end=month_end, freq='1h')

                    # 多次尝试不同随机偏移；每次在偏移网格（步长=interval_hours）上取候选并随机打乱
                    max_offset_trials = min(48, max(1, interval_hours))
                    tried_offsets = set()

                    while len(collected) < need and len(tried_offsets) < max_offset_trials:
                        if interval_hours > 1:
                            offset = int(rng.integers(0, interval_hours))
                            if offset in tried_offsets:
                                continue
                            tried_offsets.add(offset)
                        else:
                            offset = 0

                        idxs = list(range(offset, len(all_hours), interval_hours))
                        candidates = [all_hours[j] for j in idxs]
                        rng.shuffle(candidates)  # 打乱顺序，避免总是优先早期时间

                        for cdate in candidates:
                            if is_valid(cdate, existing_list, interval_hours):
                                collected.append(cdate)
                                existing_list.append(cdate)
                                if len(collected) >= need:
                                    break

                    if len(collected) < need:
                        print(f'警告: 路线{lane}，{str(yq)}（{year}-{month:02d}），重量{weight} '
                              f'目标新增{need}条，但在最小间隔{interval_hours}小时约束下仅生成{len(collected)}条。')

                    # 生成记录
                    for t in collected:
                        result_list.append({
                            'LOCATION': location,
                            'SHIPMENT LANE': lane,
                            'Truck Weight': weight,
                            'Transport Mode': 'Callout',
                            'SHIPMENT DISTANCE': lane_distance,
                            'INVOICE TYPE': 'STANDARD',
                            'SHIPMENT GATE IN SOURCE': t,
                            'SHIPMENT GATE OUT SOURCE': t + pd.Timedelta(hours=1),
                            'SHIPMENT GATE IN DESTINATION': t + avg_timedelta - pd.Timedelta(hours=1),
                            'SHIPMENT GATE OUT DESTINATION': t + avg_timedelta,
                            'SHIPMENT CREATION DATE': t,
                            'Special_Lane': special_lane_value,
                        })

    # 3. 将新增行程拼接到原始表
    new_shipments = pd.DataFrame(result_list)
    predicted_df = pd.concat([df, new_shipments], ignore_index=True)

    # 筛选新增行程：SHIPMENT MOT 列为空值的行
    new_shipments = predicted_df[predicted_df['SHIPMENT MOT'].isna()]
    # 1. 提取新增行程季度
    new_shipments['Quarter'] = new_shipments['SHIPMENT CREATION DATE'].apply(get_quarter)

    # 2. 进行按路线、重量、季度、地点分组，求SHIPMENT DISTANCE总和作为distance variance
    new_distance_variance = new_shipments.groupby(
        ['SHIPMENT LANE', 'Truck Weight', 'Quarter', 'LOCATION'], as_index=False
    )['SHIPMENT DISTANCE'].sum().rename(columns={'SHIPMENT DISTANCE': 'distance variance'})

    # 3. 重命名 LOCATION -> Location，匹配distance_variance_unique列名
    new_distance_variance.rename(columns={'LOCATION': 'Location'}, inplace=True)

    # 4. 读取已有distance_variance_unique表（如果没有则新建空DF示范）
    distance_variance_path = os.path.join(Predict_output_dir, 'distance_variance_output.csv')
    if os.path.exists(distance_variance_path):
        distance_variance_unique = pd.read_csv(distance_variance_path)
    else:
        distance_variance_unique = pd.DataFrame(
            columns=['SHIPMENT LANE', 'Truck Weight', 'Quarter', 'distance variance', 'Location'])

    # 5. 拼接新增的distance variance数据
    distance_variance_combined = pd.concat([distance_variance_unique, new_distance_variance], ignore_index=True)

    # 6. 去除重复行
    distance_variance_combined = distance_variance_combined.drop_duplicates()
    distance_variance_combined = distance_variance_combined.merge(
        df_add_percentage,
        on=['SHIPMENT LANE', 'Quarter', 'Truck Weight'],
        how='left'
    )
    # 合并重复的rate列
    distance_variance_combined['rate'] = distance_variance_combined['rate_x'].combine_first(
        distance_variance_combined['rate_y'])
    distance_variance_combined = distance_variance_combined.drop(columns=['rate_x', 'rate_y'])
    # 7. 保存合并后的distance variance表
    output_distance_variance_path = os.path.join(Predict_output_dir, 'distance_variance.csv')
    distance_variance_combined.to_csv(output_distance_variance_path, index=False, encoding='utf-8-sig')

    print(f"✅ 新增行程的distance variance已合并并保存至：{output_distance_variance_path}")
    # 基地列大写
    callout_cost['基地'] = callout_cost['基地'].str.upper()

    # 将新增行程与 callout_cost 表合并
    new_shipments = pd.merge(
        new_shipments,
        callout_cost,
        left_on=['Truck Weight', 'LOCATION'],
        right_on=['车型', '基地'],
        how='left'
    )

    # 分解路线字段
    new_shipments['SOURCE CITY'] = new_shipments['SHIPMENT LANE'].str.split('_').str[0]
    new_shipments['DESTINATION CITY'] = new_shipments['SHIPMENT LANE'].str.split('_').str[1]
    special_lane_cost = pd.read_csv(special_lane_cost_path, index_col=0)
    exchange_rate = pd.read_excel(exchange_rate_path)
    # ===============================
    # 一、计算普通路线费用（Special_Lane = 'N'）
    # ===============================
    normal_lane_df = new_shipments[new_shipments['Special_Lane'] == 'N'].copy()
    normal_lane_df['Mile Cost($)'] = normal_lane_df['每公里/车型'] * normal_lane_df['SHIPMENT DISTANCE']
    normal_lane_df['TOTAL INVOICE COST (USD)'] = normal_lane_df['Mile Cost($)']

    # ===============================
    # 二、计算特殊路线费用（Special_Lane = 'Y'）
    # ===============================
    special_lane_df = new_shipments[new_shipments['Special_Lane'] == 'Y'].copy()

    # 1. 匹配特殊路线费用（人民币）
    def get_special_lane_fee(row):
        try:
            return special_lane_cost.loc[row['车型'], row['SHIPMENT LANE']]
        except KeyError:
            return np.nan

    special_lane_df['Special_lane Cost(￥)'] = special_lane_df.apply(get_special_lane_fee, axis=1)
    # 2. 提取年月列 YYYY/MM
    special_lane_df['YYYYMM'] = pd.to_datetime(
        special_lane_df['SHIPMENT CREATION DATE'], errors='coerce'
    ).dt.strftime('%Y/%m')
    # 💡 关键修改：确保 exchange_rate['YYYYMM'] 也是字符串格式
    exchange_rate['YYYYMM'] = pd.to_datetime(
        exchange_rate['YYYYMM'], errors='coerce'
    ).dt.strftime('%Y/%m')

    # 3. 合并汇率并换算为美元
    special_lane_df = pd.merge(
        special_lane_df,
        exchange_rate[['YYYYMM', 'Dollar_RMB_rate']],
        on='YYYYMM',
        how='left'
    )

    special_lane_df['Special_lane Cost($)'] = special_lane_df['Special_lane Cost(￥)'] / special_lane_df[
        'Dollar_RMB_rate']

    # 4. 检查未匹配费用记录
    unmatched_rows = special_lane_df[special_lane_df['Special_lane Cost(￥)'].isna()]
    if not unmatched_rows.empty:
        print("以下记录未匹配到特殊路线费用，请检查 special_lane_cost 表里的 SHIPMENT LANE 和 车型：")
        print(unmatched_rows[['Truck Plate', 'SHIPMENT LANE', '车型']])

    # 5. 四舍五入
    special_lane_df['Special_lane Cost(￥)'] = special_lane_df['Special_lane Cost(￥)'].round(2)
    special_lane_df['Special_lane Cost($)'] = special_lane_df['Special_lane Cost($)'].round(2)

    # 6. 写入总费用列
    special_lane_df['TOTAL INVOICE COST (USD)'] = special_lane_df['Special_lane Cost($)']
    # ===============================
    # 三、合并普通与特殊路线结果
    # ===============================
    calculated_df = pd.concat([normal_lane_df, special_lane_df], ignore_index=True)

    # 删除与 callout_cost 表合并后新增的临时列
    columns_to_drop = ['车型', '基地', '每公里/车型', '费用/每天', 'Mile Cost($)',
                       'Special_lane Cost(￥)', 'Special_lane Cost($)', 'YYYYMM']
    calculated_df.drop(columns=[col for col in columns_to_drop if col in calculated_df.columns], inplace=True)

    # ===============================
    # 四、合并回主表
    # ===============================
    existing_shipments = predicted_df[predicted_df['SHIPMENT MOT'].notna()]
    predicted_df = pd.concat([existing_shipments, calculated_df], ignore_index=True)
    predicted_df = predicted_df[predicted_df['Transport Mode'] == 'Callout']

    # 在调用 callout_trip_concat 函数之前，复制 Truck Weight 列为 weight 列
    predicted_df['weight'] = predicted_df['Truck Weight']

    # 删除 weight 列中含有 NaN 的行
    predicted_df = predicted_df.dropna(subset=['weight'])
    # ===============================
    # 五、保存结果与后续调用
    # ===============================
    predicted_df = predicted_df.drop(columns=['Dollar_RMB_rate'])
    os.makedirs(Predict_output_dir, exist_ok=True)
    output_path = os.path.join(Predict_output_dir, "future_callout_shipments.csv")
    predicted_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"✅ 预测结果已保存至：{output_path}")

    # ==============================
    # 3. 后续处理：合并与费用预测
    # ==============================
    concat_output_dir = os.path.join(Predict_output_dir, "concat results")
    os.makedirs(concat_output_dir, exist_ok=True)

    callout_trip_concat(output_path, transit_time_path, concat_output_dir, min_chain_len=10)
    simulate_callout_to_rental(
        folder_path=concat_output_dir,
        rental_cost_path=rental_cost_path,
        output_path=os.path.join(Predict_output_dir, "Predict callout_to_rental cost.csv"),
        exchange_rate_path=exchange_rate_path,
        dispatch_time_path=transit_time_path
    )

    print("🎯 Callout 预测流程完成！")
    return predicted_df