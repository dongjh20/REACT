# 短入口距离下的 TVFR 时长调整

## 设计

第一处通道恢复到历史位置 [-2,10] m 后，机器人更早受到墙体约束，但原参考仍需 12 s
完成切换。直接把统一时长减到 10 s，会同时加快横向收拢与纵向槽位调整，可能加重纵向减速。

新增可选阶段参数 `lateral_transition_duration`：

```yaml
experiment:
  stages:
  - type: 2
    transition_duration: 12.0
    lateral_transition_duration: 10.0
    # 其他阶段参数及 slots 沿用原配置
```

`transition_duration` 继续控制 world-x/z；可选参数只控制 world-y。
这是当前世界坐标对齐槽位设计下的分轴时长，不是沿任意曲线路线的 Frenet 横纵向变换。
省略新参数时，各轴仍使用原时长；瞬时切换阶段不允许单独启用横向过渡。

每一轴均使用原来的五次平滑插值，位置、速度、加速度解析导数同步计算。
相对位置、相对速度和图结构代价的时间梯度继续使用这些逐轴导数，没有另加代价函数、
执行端位置滤波、速度钳制或修改历史测量数据。过渡活动状态持续到最慢的轴完成。

## 配置与实跑

不修改日常 `icra_s_curve.yaml` 的宽入口场景，只生成独立同环境实验配置：

```bash
cd /home/focus/ros2_ws/src/Multi-Lane-Vehicle-Formation-ROS2
python3 tools/icra/prepare_matched_environment.py \
  --lateral-32-seconds 10 \
  --output result/ICRA-video/comparisons/matched_environment_xy12_10
source /opt/ros/humble/setup.bash
source install/setup.bash
ROS_DOMAIN_ID=184 ROS_LOCALHOST_ONLY=1 \
python3 src/planner/plan_manage/test/run_icra_experiment.py \
  --config result/ICRA-video/comparisons/matched_environment_xy12_10/latest_method_original_environment.yaml
```

新增 ROS 参数 `formation_switch/lateral_transition_durations` 由同一 YAML 加载器展开到各车，
实验记录器逐车回读并校验，不能只依赖配置文件判断生效。
新功能需要重新编译 `swarm_graph`、`traj_opt`、`ego_planner`。

## 评价方法

所有候选保留完整记录。用相同切换后 [0,16] s 窗口及 0.02 s 采样比较：
横向多走距离、横向速度峰值、航向角峰值、前向速度下限、加速度 RMS 和倒退距离。
同时要求原有完整运行、采样碰撞、双列与末尾三列收敛检查通过。

```bash
python3 tools/icra/evaluate_transition_tuning.py <原记录> <候选记录> <重复记录> \
  --output result/ICRA-video/comparisons/tvfr_tuning/evaluation.json
```

各次完整运行的森林出口状态存在变化；少量重复验证不能替代严格统计或完全相同状态的单因素对照。
结果与最终采用方案见 `result/ICRA-video/comparisons/tvfr_tuning/README.md`。

## 测试范围

- 不配置新参数时保持原插值；配置时 y 可以先于或晚于 x 完成。
- 过渡前后速度、加速度的端点连续性及相对代价时间梯度数值核验。
- YAML 非正数/非有限数及瞬时阶段误用被拒绝；7 个规划器参数一致。
- 五次插值在夹紧端点为 C²、非 C³；端点有限差分采用更小步长，误差阈值未放宽。
