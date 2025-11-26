import pandas as pd
from .fee_model import calculate_rental_cost, calculate_callout_cost, calculate_special_lane_cost

def rental_to_callout(
        rental_info_path,
        rental_truck_list_path,
        exchange_rate_path,
        callout_cost_path,
        special_lane_cost_path,
        equipment_vehicle_type_path,
        output_path_callout_cost,
        rental_cost,
        output_path_special_lane_cost,
):
    """
    根据rental_info计算月租转外叫费用情况

    参数说明：
    - rental_info_path: 月租信息CSV文件路径，同时作为callout_info的输入
    - rental_truck_list_path: 租赁车辆列表CSV路径
    - exchange_rate_path: 汇率表CSV路径
    - callout_cost_path: 外叫费用标准CSV路径
    - special_lane_cost_path: 特殊路线费用标准CSV路径
    - equipment_vehicle_type_path: 车型信息CSV路径
    - output_path_callout_cost: 中间外叫费用结果保存路径
    - output_path_special_lane_cost: 中间特殊路线费用结果保存路径

    返回：
    - DataFrame，包含每辆车每月的月租费用、外叫费用、特殊路线费用及是否转外叫判定
    """

    # 1. 计算外叫费用 （传入rental_info_path作为callout_info）
    callout_df = calculate_callout_cost(
        callout_info_path=rental_info_path,
        callout_cost_path=callout_cost_path,
        rental_truck_list_path=rental_truck_list_path,
        exchange_rate_path=exchange_rate_path,
        output_path=output_path_callout_cost
    )

    # 按 Truck Plate 和 YYYYMM 汇总外叫费用 (美元)
    callout_df['YYYYMM'] = pd.to_datetime(callout_df['SHIPMENT CREATION DATE'], errors='coerce').dt.strftime('%Y/%m')
    if 'EQUIPMENT ID_x' in callout_df.columns and 'EQUIPMENT ID_y' in callout_df.columns:
        # 以 EQUIPMENT ID_x 为准，删除 EQUIPMENT ID_y
        callout_df = callout_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
        callout_df = callout_df.drop(columns=['EQUIPMENT ID_y'])
    elif 'EQUIPMENT ID_x' in callout_df.columns:
        callout_df = callout_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
    elif 'EQUIPMENT ID_y' in callout_df.columns:
        callout_df = callout_df.rename(columns={'EQUIPMENT ID_y': 'EQUIPMENT ID'})

    callout_monthly = callout_df.groupby(
        ['Truck Plate', 'LOCATION', 'YYYYMM'],
        as_index=False
    ).agg({
        'Total Cost($)': 'sum'
    }).rename(columns={'Total Cost($)': 'Total Callout_Cost($)'})

    # 2. 计算特殊路线费用 （传入rental_info_path作为callout_info）
    special_lane_df = calculate_special_lane_cost(
        callout_info_path=rental_info_path,
        special_lane_cost_path=special_lane_cost_path,
        rental_truck_list_path=rental_truck_list_path,
        exchange_rate_path=exchange_rate_path,
        output_path=output_path_special_lane_cost
    )
    special_lane_df['YYYYMM'] = pd.to_datetime(special_lane_df['SHIPMENT CREATION DATE'], errors='coerce').dt.strftime(
        '%Y/%m')

    # 如果有 Truck Plate_x 和 Truck Plate_y，选择一个重命名为 Truck Plate，删除另一个
    if 'Truck Plate_x' in special_lane_df.columns and 'Truck Plate_y' in special_lane_df.columns:
        # 以 Truck Plate_x 为准，删除 Truck Plate_y
        special_lane_df = special_lane_df.rename(columns={'Truck Plate_x': 'Truck Plate'})
        special_lane_df = special_lane_df.drop(columns=['Truck Plate_y'])
    elif 'Truck Plate_x' in special_lane_df.columns:
        special_lane_df = special_lane_df.rename(columns={'Truck Plate_x': 'Truck Plate'})
    elif 'Truck Plate_y' in special_lane_df.columns:
        special_lane_df = special_lane_df.rename(columns={'Truck Plate_y': 'Truck Plate'})
    if 'EQUIPMENT ID_x' in special_lane_df.columns and 'EQUIPMENT ID_y' in special_lane_df.columns:
        # 以 EQUIPMENT ID_x 为准，删除 EQUIPMENT ID_y
        special_lane_df = special_lane_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
        special_lane_df = special_lane_df.drop(columns=['EQUIPMENT ID_y'])
    elif 'EQUIPMENT ID_x' in special_lane_df.columns:
        special_lane_df = special_lane_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
    elif 'EQUIPMENT ID_y' in special_lane_df.columns:
        special_lane_df = special_lane_df.rename(columns={'EQUIPMENT ID_y': 'EQUIPMENT ID'})
    # 按 Truck Plate 和 YYYYMM 汇总特殊路线费用 (美元)
    special_lane_monthly = special_lane_df.groupby(
        ['Truck Plate', 'YYYYMM'],
        as_index=False
    ).agg({
        'Special_lane Cost($)': 'sum'
    }).rename(columns={'Special_lane Cost($)': 'Total Special_lane_Cost($)'})

    # 3. 外叫费用和特殊路线费用合并，计算月租转外叫总费用
    callout_total_monthly = pd.merge(
        callout_monthly,
        special_lane_monthly,
        on=['Truck Plate', 'YYYYMM'],
        how='outer'
    ).fillna(0)
    callout_total_monthly['Rental_to_Callout Cost($)'] = (
            callout_total_monthly['Total Callout_Cost($)'] +
            callout_total_monthly['Total Special_lane_Cost($)']
    )

    # 4. 计算月租费用
    rental_monthly_df = calculate_rental_cost(
        rental_info_path=rental_info_path,
        rental_truck_list_path=rental_truck_list_path,
        exchange_rate_path=exchange_rate_path,
        output_path=None  # 不输出文件，返回DataFrame
    )

    # 确保 YYYYMM 格式一致
    rental_monthly_df['YYYYMM'] = rental_monthly_df['YYYYMM'].astype(str)

    # 只取 Truck Plate 和 Total Rental Cost 列
    rental_monthly_df = rental_monthly_df[['Truck Plate', '车型', 'LOCATION', 'YYYYMM', 'Total Rental Cost']]

    # 5. 合并月租费用和月租转外叫费用
    compare_df = pd.merge(
        rental_monthly_df,
        callout_total_monthly[['Truck Plate', 'LOCATION', 'YYYYMM', 'Rental_to_Callout Cost($)']],
        on=['Truck Plate', 'YYYYMM'],
        how='outer',
        suffixes=('', '_drop')  # 设置后缀，避免 LOCATION 列冲突
    ).fillna(0)
    # 删除多余的 LOCATION 列
    if 'LOCATION_drop' in compare_df.columns:
        compare_df.drop(columns=['LOCATION_drop'], inplace=True)

    # 6.计算 Saving($) 等于两者的差值
    compare_df['Saving($)'] = compare_df['Total Rental Cost'] - compare_df['Rental_to_Callout Cost($)']

    # 7. 汇总所有月份费用，按Truck Plate分组
    summary_df = compare_df.groupby(['Truck Plate', 'LOCATION'], as_index=False).agg({
        'Total Rental Cost': 'sum',
        'Rental_to_Callout Cost($)': 'sum'
    })
    # 判定汇总是否转外叫
    summary_df['是否转外叫'] = summary_df['Rental_to_Callout Cost($)'] < summary_df['Total Rental Cost']
    # 计算节约费用（当转外叫时，月租费用 - 外叫费用，大于0时有效）
    summary_df['Saving($)'] = summary_df.apply(
        lambda row: max(row['Total Rental Cost'] - row['Rental_to_Callout Cost($)'], 0) if row['是否转外叫'] else 0,
        axis=1
    )

    # 8. 列重命名统一
    compare_df.rename(columns={
        'Total Rental Cost': '月租费用($)',
        'Rental_to_Callout Cost($)': '外叫总费用($)'
    }, inplace=True)

    # 9. 返回结果
    return compare_df, summary_df

