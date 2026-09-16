#!/usr/bin/env python3
"""Independent read-only parameter evidence; does not modify historical nodes."""
import argparse
import json
from pathlib import Path
import time
import rclpy
from rcl_interfaces.srv import GetParameters
from rclpy.parameter import parameter_value_to_python


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args=ap.parse_args()
    names=['fsm/finish_distance','global_goal/route','formation_switch/stage_types',
           'optimization/obstacle_clearance','optimization/planar_motion','formation_model/exact_gradient',
           'manager/planning_horizon','fsm/planning_horizon','grid_map/local_update_range_x',
           'grid_map/local_update_range_y','formation_switch/shape_weights',
           'optimization/max_acc','manager/max_acc']
    requests={f'/drone_{i}_ego_planner_node': names for i in range(7)}
    requests.update({f'/drone_{i}_pcl_render_node':['sensing_horizon','horizontal_fov_deg'] for i in range(7)})
    requests['/formation_manager']=['trigger_x','stage_require_all_past','stage_all_past_x']
    rclpy.init();node=rclpy.create_node('historical_parameter_evidence')
    clients={key:node.create_client(GetParameters,key+'/get_parameters') for key in requests}
    pending={};responses={};started=time.monotonic()
    try:
        while len(responses)<len(requests) and time.monotonic()-started<20:
            for key,client in clients.items():
                if key in responses:continue
                if key in pending:
                    future,stamp=pending[key]
                    if future.done():
                        values=future.result().values
                        if len(values)==len(requests[key]):
                            responses[key]=dict(zip(requests[key],map(parameter_value_to_python,values)))
                        del pending[key]
                    elif time.monotonic()-stamp>2:
                        client.remove_pending_request(future);del pending[key]
                if key not in pending and key not in responses and client.service_is_ready():
                    pending[key]=(client.call_async(GetParameters.Request(names=requests[key])),time.monotonic())
            rclpy.spin_once(node,timeout_sec=.02)
    finally:
        node.destroy_node();rclpy.shutdown()
    evidence=dict(complete=len(responses)==len(requests),response_count=len(responses),parameters=responses,
                  purpose='Independent live readback, no node parameter writes; original recorder reports are not modified')
    args.output.write_text(json.dumps(evidence,indent=2)+'\n')
    print(f'Parameter responses: {len(responses)}/{len(requests)}')
    return 0 if evidence['complete'] else 1


if __name__=='__main__':raise SystemExit(main())
