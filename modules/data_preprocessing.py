import pandas as pd
import re

def data_quality_check(df, city_location, seg_division,
                       rental_cost_df=None, callout_cost_df=None,
                       special_lane_cost_df=None, transit_time_path=None):
    """
    数据质量检查，包括：
    - 主表中 SOURCE CITY 和 OR SEGMENT 空值提醒
    - LOCATION 和 Division 的 PENDING 提醒
    - city_location 中 Parent City 列是否有重复值提醒
    - seg_division 中 Seg 列是否有重复值提醒
    - SHIPMENT GATE OUT SOURCE 与 SHIPMENT GATE IN DESTINATION 时间差检查
    """

    messages = []  # 用于收集所有提醒信息

    # 1. 主表中 SOURCE CITY 空值检测
    missing_source_city = df[df['SOURCE CITY'].isna()]
    if not missing_source_city.empty:
        messages.append("以下 SHIPMENT ID 的记录缺少 SOURCE CITY，请补充该信息：")
        for shipment_id in missing_source_city['SHIPMENT ID']:
            messages.append(f"  SHIPMENT ID: {shipment_id} 的 SOURCE CITY 信息缺失！")

    # 2. 主表中 OR SEGMENT 空值检测
    missing_segment = df[df['OR SEGMENT'].isna()]
    if not missing_segment.empty:
        messages.append("以下 SHIPMENT ID 的记录缺少 OR SEGMENT，请补充该信息：")
        for shipment_id in missing_segment['SHIPMENT ID']:
            messages.append(f"  SHIPMENT ID: {shipment_id} 的 OR SEGMENT 信息缺失！")

    # 3. LOCATION 和 Division 的 PENDING 检测
    pending_cities = df[df['LOCATION'] == 'PENDING']['SOURCE CITY'].unique()
    pending_division = df[df['Division'] == 'PENDING']['OR SEGMENT'].unique()

    if len(pending_division) > 0:
        messages.append("以下 OR SEGMENT 未在 segment-division 表中找到对应的 Seg，请更新 segment-division 表：")
        for seg in pending_division:
            messages.append(f"  {seg} 未在 segment-division 表中更新，请在该表添加 Seg 信息。")

    if len(pending_cities) > 0:
        messages.append("以下 SOURCE CITY 未在 city-location 表中找到对应的 LOCATION，请更新 city-location 表：")
        for city in pending_cities:
            messages.append(f"  {city} 未在 city-location 表中更新，请在该表添加 LOCATION 信息。")

    # 4. city-location 表 Parent City 重复项检测
    duplicate_parent_city = city_location[city_location.duplicated(subset=['Parent City'], keep=False)]
    if not duplicate_parent_city.empty:
        messages.append("city-location 表中发现重复的 Parent City，可能影响匹配，请人工核查和删除重复项：")
        for val in duplicate_parent_city['Parent City'].unique():
            messages.append(f"  重复 Parent City: {val}")

    # 5. segment-division 表 Seg 重复项检测
    duplicate_seg = seg_division[seg_division.duplicated(subset=['Seg'], keep=False)]
    if not duplicate_seg.empty:
        messages.append("segment-division 表中发现重复的 Seg，可能影响匹配，请人工核查和删除重复项：")
        for val in duplicate_seg['Seg'].unique():
            messages.append(f"  重复 Seg: {val}")

    # 6. 检查是否缺少 四个时间 中的任意值
    columns_to_copy = [
        'SHIPMENT GATE IN SOURCE',
        'SHIPMENT GATE OUT SOURCE',
        'SHIPMENT GATE IN DESTINATION',
        'SHIPMENT GATE OUT DESTINATION'
    ]

    # 遍历主表中的每一行，检查是否缺少 columns_to_copy 中的任意列
    messages.append("以下 SHIPMENT ID 的四个时间不完整，请核对时间：")
    for _, row in df.iterrows():
        missing_data = [col for col in columns_to_copy if pd.isna(row.get(col))]
        if missing_data:
            messages.append(f"SHIPMENT ID: {row['SHIPMENT ID']} 缺少以下列的数据：{', '.join(missing_data)}")

    # 7. 检查 SHIPMENT GATE OUT SOURCE 与 SHIPMENT GATE IN DESTINATION 时间差是否大于7天
    def parse_datetime_col(series):
        series = series.astype(str).str.strip().str.replace(r'\s+', ' ', regex=True)
        return pd.to_datetime(series, errors='coerce')

    out_source_time = parse_datetime_col(df['SHIPMENT GATE OUT SOURCE'])
    in_destination_time = parse_datetime_col(df['SHIPMENT GATE IN DESTINATION'])

    time_diff_days = (in_destination_time - out_source_time).abs().dt.total_seconds() / (3600*24)

    idx_abnormal = time_diff_days > 7

    if idx_abnormal.any():
        messages.append("以下 SHIPMENT ID 的发车时间与到达时间相差超过7天，请核对时间：")
        for shipment_id in df.loc[idx_abnormal, 'SHIPMENT ID']:
            messages.append(f"  SHIPMENT ID: {shipment_id}")


    # 8. 外叫车费用相关检查
    if callout_cost_df is not None:
        missing_cost = callout_cost_df[callout_cost_df['每公里/车型'].isna()]
        if not missing_cost.empty:
            messages.append("以下记录未匹配到 callout cost 表，请检查车型和基地：")
            for idx, row in missing_cost.iterrows():
                messages.append(
                    f"  - Truck Plate: {row.get('Truck Plate', '未知')}, 车型: {row.get('车型', '未知')}, LOCATION: {row.get('LOCATION', '未知')}")

        time_columns = [
            'SHIPMENT GATE OUT SOURCE',
            'SHIPMENT GATE IN SOURCE',
            'SHIPMENT GATE IN DESTINATION',
            'SHIPMENT GATE OUT DESTINATION'
        ]
        for col in time_columns:
            if col in callout_cost_df.columns:
                missing_time = callout_cost_df[callout_cost_df[col].isna()]
                if not missing_time.empty:
                    messages.append(f"以下记录 {col} 列存在空值，请补充数据：")
                    for idx, row in missing_time.iterrows():
                        messages.append(f"  - Truck Plate: {row.get('Truck Plate', '未知')}")


    # 9. 特殊路线费用相关检查
    if special_lane_cost_df is not None:
        missing_vehicle = special_lane_cost_df[special_lane_cost_df['车型'].isna()]
        if not missing_vehicle.empty:
            messages.append("特殊路线费用表中存在未提取车型信息的记录，请检查：")
            for idx, row in missing_vehicle.iterrows():
                messages.append(f"  - EQUIPMENT ID: {row.get('EQUIPMENT ID', '未知')}, SHIPMENT LANE: {row.get('SHIPMENT LANE', '未知')}")

        missing_cost = special_lane_cost_df[special_lane_cost_df['Special_lane Cost($)'].isna()]
        if not missing_cost.empty:
            messages.append("特殊路线费用表中存在未匹配到费用的记录，请检查 SHIPMENT LANE 和 车型：")
            for idx, row in missing_cost.iterrows():
                messages.append(f"  - EQUIPMENT ID: {row.get('EQUIPMENT ID', '未知')}, SHIPMENT LANE: {row.get('SHIPMENT LANE', '未知')}, 车型: {row.get('车型', '未知')}")

    # 10. 调车时间表检查（只对 SHIPMENT LANE 前后城市不一致的记录进行检查）
    transit_time_df = pd.read_csv(transit_time_path) if transit_time_path else None
    if transit_time_df is not None:
        lane_dist_dict = dict(zip(transit_time_df['LANE'], transit_time_df['Lane_Distance']))
        abnormal_rows = []
        for idx, row in df.iterrows():
            shipment_lane = row.get('SHIPMENT LANE')
            shipment_dist = row.get('SHIPMENT DISTANCE')

            # 跳过空值
            if pd.isna(shipment_lane) or pd.isna(shipment_dist):
                continue

            # 检查 SHIPMENT LANE 的前后城市是否一致
            cities = shipment_lane.split('_')
            if len(cities) == 2 and cities[0] == cities[1]:
                # 如果城市一致（如 CHENGDU_CHENGDU），跳过检查
                continue

            # 获取 Lane_Distance
            lane_dist = lane_dist_dict.get(shipment_lane)
            if lane_dist is None or pd.isna(lane_dist):
                continue

            # 检查 SHIPMENT DISTANCE 是否超过 Lane_Distance 的 5 倍
            if shipment_dist > 5 * lane_dist:
                abnormal_rows.append((row['SHIPMENT ID'], shipment_dist, lane_dist, shipment_lane))

        if abnormal_rows:
            messages.append(
                "以下 SHIPMENT ID 的 SHIPMENT DISTANCE 超过调车时间表 Lane_Distance 的5倍，请检查数据合理性：")
            for shipment_id, ship_dist, lane_dist, shipment_lane in abnormal_rows:
                messages.append(
                    f"  SHIPMENT ID: {shipment_id}，LANE：{shipment_lane}，SHIPMENT DISTANCE: {ship_dist}，Lane_Distance: {lane_dist}")

    # 11. 检查 SHIPMENT DISTANCE 是否少于 10，且排除 SHIPMENT LANE 前后城市一致的情况
    shipment_distance_below_10 = df[df['SHIPMENT DISTANCE'] < 10]

    # 过滤掉 SHIPMENT LANE 前后城市一致的行
    def is_same_city_lane(lane):
        if pd.isna(lane):  # 如果 SHIPMENT LANE 是空值，返回 False
            return False
        cities = lane.split('_')  # 按下划线分割城市
        return len(cities) == 2 and cities[0] == cities[1]  # 判断前后城市是否一致

    shipment_distance_below_10 = shipment_distance_below_10[
        ~shipment_distance_below_10['SHIPMENT LANE'].apply(is_same_city_lane)]

    # 如果还有符合条件的数据，打印 EQUIPMENT ID 信息
    if not shipment_distance_below_10.empty:
        messages.append("以下 EQUIPMENT ID 的 SHIPMENT DISTANCE 少于 10，请核对数据：")
        for _, row in shipment_distance_below_10.iterrows():  # 使用 iterrows() 遍历行
            shipment_id = row['SHIPMENT ID']
            shipment_lane = row.get('SHIPMENT LANE', '未知')  # 使用 .get() 避免列缺失
            ship_dist = row['SHIPMENT DISTANCE']
            messages.append(f"  SHIPMENT ID: {shipment_id}, LANE: {shipment_lane}, SHIPMENT DISTANCE: {ship_dist}")


    # 如果没有任何提醒，返回提示无异常
    if not messages:
        messages.append("未发现数据质量问题。")

    # 返回所有消息，换行连接
    return "\n".join(messages)


def data_preprocessing(file_path, city_location_path, seg_division_path, special_lane_cost_path, rental_truck_list_path, start_date_str, end_date_str):
    """
    数据预处理同之前，略（保持不变）
    """
    # 读取数据
    df = pd.read_csv(file_path)
    city_location = pd.read_csv(city_location_path)
    seg_division = pd.read_csv(seg_division_path)
    special_lane_cost = pd.read_csv(special_lane_cost_path)
    rental_truck_list = pd.read_csv(rental_truck_list_path)

    # 1 - 7 步骤清洗略 同之前代码
    # 1. SHIPMENT ID 不能为空，不能重复
    df = df.dropna(subset=['SHIPMENT ID'])
    df = df.drop_duplicates(subset=['SHIPMENT ID'])

    # 2. SHIPMENT NAME 不能是 "customs shipment"
    df = df[df['SHIPMENT NAME'] != 'customs shipment']

    # 3. SHIPMENT SOURCE COUNTRY 不能是 "HKG"
    df = df[df['SHIPMENT SOURCE COUNTRY'] != 'HKG']

    # 4. SERVPROV NAME 不能包含 "TIANBAO", "BEIJING VIBRANT", "CHN_FLEET"
    exclude_servprov = ['TIANBAO', 'BEIJING VIBRANT', 'CHN_FLEET']
    df = df[~df['SERVPROV NAME'].str.contains('|'.join(exclude_servprov), na=False)]

    # 5. SHIPMENT CREATED BY 和 TENDERED BY 不能是以下用户：
    exclude_users = ['XCHEN48', 'YFENG21', 'YFU6', 'YLI122', 'YLI179', 'YLV3']
    df = df[~df['SHIPMENT CREATED BY'].isin(exclude_users)]
    df = df[~df['TENDERED BY'].isin(exclude_users)]

    # 6. IS SHIPMENT CANCELLED 不能是 "Y"
    df = df[df['IS SHIPMENT CANCELLED'] != 'Y']

    # 7. ENROUTE 列不能是 "ENROUTE_NOT STARTED" 或空值
    df = df[(df['ENROUTE'] != 'ENROUTE_NOT STARTED') & (df['ENROUTE'].notna())]

    # 8. 删除特殊情况
    df = df[(df['ORDER RELEASE'] != 'TR2500518175')]

    # 9. 提取 EQIUPMENT ID 列的后六位字符，创建新列 Truck Plate
    df['Truck Plate'] = df['EQUIPMENT ID'].str[-6:]  # 使用 str[-6:] 提取后六位字符

    # 10. 特殊路线、月租车和外叫车的数据提取
    special_lane_cost_t = special_lane_cost.T
    special_routes = special_lane_cost_t.index.tolist()
    special_routes_split = [route.split('_') for route in special_routes]

    # 11. 提取截至日期的数据
    df['SHIPMENT CREATION DATE'] = pd.to_datetime(df['SHIPMENT CREATION DATE'], format='%m/%d/%Y %I:%M:%S %p',
                                                  errors='coerce')


    # 处理日期转换失败的行（optional）
    if df['SHIPMENT CREATION DATE'].isnull().any():
        print("警告：部分 SHIPMENT CREATION DATE 无法解析，将被过滤")
        df = df.dropna(subset=['SHIPMENT CREATION DATE'])

    # 处理开始日期
    if start_date_str:
        try:
            start_date = pd.to_datetime(start_date_str)
            # 设定开始日期的当天0点
            start_datetime = pd.Timestamp(start_date.year, start_date.month, start_date.day, 0, 0, 0)
        except Exception as e:
            raise ValueError(f"输入的开始日期格式错误: {start_date_str}，请使用YYYY-MM-DD格式") from e

        # 筛选大于等于开始日期的行
        df = df[df['SHIPMENT CREATION DATE'] >= start_datetime]

    # 如果有传截止时间参数，做筛选
    if end_date_str:
        try:
            # 解析输入的截止日期字符串，默认格式 YYYY-MM-DD
            end_date = pd.to_datetime(end_date_str)
            # 设定截止日期的当天结束时间
            end_datetime = pd.Timestamp(end_date.year, end_date.month, end_date.day, 23, 59, 59)
        except Exception as e:
            raise ValueError(f"输入的截止日期格式错误: {end_date_str}，请使用YYYY-MM-DD格式") from e

        # 筛选日期小于等于截止日期的行
        df = df[df['SHIPMENT CREATION DATE'] <= end_datetime]

    # 将月租车10T的换成8T,将EK7921换为EK7922
    replacement_map = {
        'CN_RTL_KJCL_FB-SW-30FT-10T_M67471': 'CN_RTL_KJCL_FB-SW-30FT-8T_M67471',
        'CN_RTL_KJCL_FB-SW-30FT-10T_M60670': 'CN_RTL_KJCL_FB-SW-30FT-8T_M60670'
    }
    df['EQUIPMENT ID'] = df['EQUIPMENT ID'].replace(replacement_map)
    replacement_map_1 = {'EK7921': 'EK7922'}
    df['Truck Plate'] = df['Truck Plate'].replace(replacement_map_1)

    # 新增一列 SHIPMENT LANE，内容为 SOURCE CITY 和 DESTINATION CITY 拼接
    df['SHIPMENT LANE'] = df['SOURCE CITY'].astype(str) + '_' + df['DESTINATION CITY'].astype(str)
    df['Special_Lane'] = df.apply(
        lambda row: 'Y' if any((row['SOURCE CITY'] == route[0] and row['DESTINATION CITY'] == route[1]) for route in special_routes_split) else 'N',
        axis=1
    )

    df['Transport Mode'] = df['SHIPMENT MOT'].map({
        'RTL-SC': 'Rental',
        'TL-RTL': 'Rental',
        'TL': 'Callout'
    })
    df.loc[df['SHIPMENT MOT'] == 'TL-RTL', 'SHIPMENT DISTANCE'] = 0 #将 TL-RTL 的 SHIPMENT DISTANCE 设置为 0
    df = df[df['Transport Mode'].notna()]  # 保留 Transport Mode 不为空的行
    mask_callout = df['Transport Mode'].astype(str).str.strip().eq('Callout')
    df = df[~((df['Transport Mode'] == 'Callout') &
              (df['EQUIPMENT ID'].str.contains(r'(FF|RA|DG)$', na=False) | df['EQUIPMENT ID'].isna()))]

    # 8. 匹配 LOCATION 和 Division
    # 处理 Transport Mode 为 'Callout' 的行
    callout_rows = df[df['Transport Mode'] == 'Callout']
    if not callout_rows.empty:
        # 与 city_location 表连接
        callout_rows = pd.merge(callout_rows, city_location, left_on='SOURCE CITY', right_on='Parent City', how='left')
        callout_rows['LOCATION'] = callout_rows['LOCATION']  # 假设 city_location 表的目标列是 'Location'
        # 检查冲突列
        if 'EQUIPMENT ID_x' in callout_rows.columns and 'EQUIPMENT ID_y' in callout_rows.columns:
            callout_rows = callout_rows.drop(columns=['EQUIPMENT ID_y'])  # 删除右表的 EQUIPMENT ID
            callout_rows = callout_rows.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})  # 重命名原表的列
    # 处理 Transport Mode 为 'Rental' 的行
    rental_rows = df[df['Transport Mode'] == 'Rental']
    if not rental_rows.empty:
        # 与 rental_truck_list 表连接
        rental_rows = pd.merge(rental_rows, rental_truck_list, left_on='Truck Plate', right_on='Truck Plate',
                               how='left')
        rental_rows['LOCATION'] = rental_rows['Location'].str.upper()  # 假设 rental_truck_list 表的目标列是 'Location'
        # 检查冲突列
        if 'EQUIPMENT ID_x' in rental_rows.columns and 'EQUIPMENT ID_y' in rental_rows.columns:
            rental_rows = rental_rows.drop(columns=['EQUIPMENT ID_y'])  # 删除右表的 EQUIPMENT ID
            rental_rows = rental_rows.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})  # 重命名原表的列
    # 合并处理后的数据
    df = pd.concat([callout_rows, rental_rows], ignore_index=True)
    # 填充未匹配到的 LOCATION
    df['LOCATION'] = df['LOCATION'].fillna('PENDING')
    df = df[df['LOCATION'] != 'DAQING'] #筛除大庆基地数据
    df = pd.merge(df, seg_division, left_on='OR SEGMENT', right_on='Seg', how='left')
    df['Division'] = df['Division'].fillna('PENDING')

    # 11. 将 RTL-SC 和 TL-RTL 的数据整合（核心复制操作）
    columns_to_copy = [
        'SHIPMENT GATE IN SOURCE',
        'SHIPMENT GATE OUT SOURCE',
        'SHIPMENT GATE IN DESTINATION',
        'SHIPMENT GATE OUT DESTINATION'
    ]

    grouped = df[df['Transport Mode'] == 'Rental'].groupby('ORDER RELEASE')

    for order_release, group in grouped:
        # 确保分组中同时存在 TL-RTL 和 RTL-SC 行
        tl_rtl_row = group[group['SHIPMENT MOT'] == 'TL-RTL']
        rtl_sc_row = group[group['SHIPMENT MOT'] == 'RTL-SC']

        if tl_rtl_row.empty or rtl_sc_row.empty:
            # 跳过，打印和提示交给质量检查处理
            continue

        tl_rtl_index = tl_rtl_row.index[0]
        rtl_sc_index = rtl_sc_row.index[0]

        # 复制 TL-RTL 行指定列到 RTL-SC 行
        df.loc[rtl_sc_index, columns_to_copy] = df.loc[tl_rtl_index, columns_to_copy]


    # 12. 对'SHIPMENT ACCESSORIAL COST USD' 'SHIPMENT TOTAL COST USD'和 'TOTAL INVOICE COST (USD)' 两列除以 1.09并进行四舍五入取整
    cols_to_divide = ['TOTAL INVOICE COST (USD)']
    for col in cols_to_divide:
        if col in df.columns:
            df[col] = df[col] / 1.09  # 除以 1.09

    cols_to_round = ['SHIPMENT ACCESSORIAL COST USD', 'SHIPMENT TOTAL COST USD', 'TOTAL INVOICE COST (USD)']
    for col in cols_to_round:
        if col in df.columns:
            df[col] = df[col].round(2)  # 使用Int64支持缺失值，否则用int

    cols_to_round_1 = ['SHIPMENT DISTANCE']
    for col in cols_to_round_1:
        if col in df.columns:
            df[col] = df[col].round(0)  # 使用Int64支持缺失值，否则用int

    # 13. 筛选需要使用的标签
    used_columns = [
        'SHIPMENT ID', 'SHIPMENT NAME', 'EQUIPMENT ID', 'SOURCE CITY', 'DESTINATION CITY', 'LOCATION', 'OR SEGMENT',
        'Division', 'Truck Plate', 'SHIPMENT LANE', 'Special_Lane', 'SHIPMENT DISTANCE', 'SHIPMENT SOURCE COUNTRY', 'Transport Mode',
        'SERVPROV NAME', 'SHIPMENT CREATED BY', 'TENDERED BY', 'IS SHIPMENT CANCELLED', 'ENROUTE', 'ORDER RELEASE', 'CARRIER REMARKS',
        'SHIPMENT GATE IN SOURCE', 'SHIPMENT GATE OUT SOURCE', 'SHIPMENT GATE IN DESTINATION', 'SHIPMENT GATE OUT DESTINATION',
        'SHIPMENT CREATION DATE', 'SHIPMENT MOT', 'INVOICE TYPE', 'SHIPMENT ACCESSORIAL COST USD', 'SHIPMENT TOTAL COST USD',
        'TOTAL INVOICE COST (USD)'
    ]
    df = df[used_columns]

    return df, city_location, seg_division
