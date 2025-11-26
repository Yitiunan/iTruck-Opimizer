# CHG iTruck Optimizer 使用说明

## 目录
1. [简介](#简介)
2. [软件安装与启动](#软件安装与启动)
3. [功能概述](#功能概述)
4. [操作流程详解](#操作流程详解)
    - [数据上传与路径设置](#数据上传与路径设置)
    - [数据时间段选择](#数据时间段选择)
    - [功能按钮说明](#功能按钮说明)
5. [注意事项](#注意事项)
6. [常见问题解答（FAQ）](#常见问题解答faq)
7. [联系方式](#联系方式)

---

## 简介

### 软件定位
- **名称/定位**：CHG iTruck Optimizer  
  面向 CHG 运输相关数据的预处理、成本分析、时间利用与预测的桌面工具。
- **目标用户**：需要对 OTM 数据进行多步处理、分析和预测的运维或数据分析人员。

### 技术栈
- **主语言**：Python  
- **图形界面**：Tkinter  
- **图像处理**：Pillow (PIL)  
- **运行机制**：多线程后台执行耗时任务，保持 UI 响应  
- **业务逻辑**：集中在主函数模块

### 运转平台与部署
- **运行平台**：Windows 桌面应用  
- **联网要求**：本地离线处理，无需网络请求  
- **数据源与输出**：  
  - 输入：`./data source/` 下的 CSV/Excel 文件  
  - 输出：`results`、`concat results`、`update results` 文件夹，包含清洗数据、模拟结果、预测结果等。

---

## 软件安装与启动
无需安装 Python 或其他编程工具，只需：
1. 将 **CHG iTruck Optimizer** 文件夹复制到本地。
2. 双击 `CHG iTruck Optimizer.exe` 启动软件。
3. 确保 `image` 文件夹与程序在同一目录。

---

## 功能概述
CHG iTruck Optimizer 提供以下核心模块：
1. **数据预处理与质量检查**：清洗 OTM 原始数据，处理缺失值和异常值。
2. **费用计算**：月租车与外叫车成本分析，特殊路线费用计算。
3. **时间利用率分析**：计算月租车的时间利用率。
4. **行程拼接**：外叫车行程拼接，减少空载。
5. **费用仿真**：模拟 LTC 转月租车的费用对比。
6. **预测模块**：基于业务需求预测未来运输情况。

---

## 操作流程详解

### 数据上传与路径设置
1. 从 OTM Power BI 导出数据（选择 **Data with current layout**）。
2. 在软件中：
   - 点击 **Browse** 上传 OTM 原始数据（CSV）。
   - 点击 **Browse** 选择输出目录。
3. 点击 **Modify Default Paths** 修改默认数据源路径（城市-基地表、部门表、费用表等）。

### 数据时间段选择
- 在 **Start Date** 和 **End Date** 输入完整时间周期（格式：`YYYY-MM-DD`）。
- 点击 **Submit Start Date** 和 **Submit End Date** 提交。

### 功能按钮说明
- **Data Preprocessing & Quality Check**  
  清洗数据并输出 `rental_info.csv`、`callout_info.csv`。
- **Rental to LTC Fee Calculation**  
  计算月租车、外叫车、特殊路线费用，输出多张费用表。
- **Rental Truck Time Utilization**  
  输出月租车时间利用率表。
- **LTC Trip Concatenation**  
  拼接外叫车行程，输出 `trip_chain_n.csv`。
- **LTC to Rental Fee Simulation**  
  仿真 LTC 转月租车费用，输出 `callout_to_rental_cost.csv`。

---

## 注意事项
- 确保数据源文件格式正确（CSV/Excel）。
- 修改默认路径时，建议直接编辑原表或上传新表。
- 功能执行过程中请勿关闭程序，状态栏会显示运行状态。

---

## 常见问题解答（FAQ）
**Q：软件无法启动怎么办？**  
A：确认 `CHG iTruck Optimizer.exe` 与 `image` 文件夹在同一目录。

**Q：输出文件为空？**  
A：检查输入数据是否完整，日期筛选是否正确。

---

## 联系方式
如有问题，请联系 CHG 运维团队。
