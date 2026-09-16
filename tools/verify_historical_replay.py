#!/usr/bin/env python3
"""Independent acceptance for a historical recorder with a lost startup RPC.

Never changes report.json or permits physical failures. Requires complete live
parameter evidence; every original response must agree with that evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(directory):
    directory=Path(directory)
    r=json.loads((directory/'report.json').read_text())
    c=json.loads((directory/'config.json').read_text())
    evidence=json.loads((directory/'independent_parameter_readback.json').read_text())
    p=evidence['parameters'];count=len(c['experiment']['stages'])
    expected={'fsm/finish_distance':c['fsm']['finish_distance'],
        'global_goal/route':[v for point in c['experiment']['route'] for v in point],
        'formation_switch/stage_types':[s['type'] for s in c['experiment']['stages']],
        'optimization/obstacle_clearance':c['optimization']['obstacle_clearance'],
        'optimization/planar_motion':c['experiment']['planar_motion'],
        'formation_model/exact_gradient':c['experiment']['exact_formation_gradient'],
        'manager/planning_horizon':c['planning']['horizon'],'fsm/planning_horizon':c['planning']['horizon'],
        'grid_map/local_update_range_x':c['grid_map']['local_update_range_x'],
        'grid_map/local_update_range_y':c['grid_map']['local_update_range_y'],
        'formation_switch/shape_weights':[s.get('shape_weight',0.) for s in c['experiment']['stages']],
        'optimization/max_acc':c['motion']['max_acc'],'manager/max_acc':c['motion']['max_acc']}
    sensors={k:c['local_sensing'][k] for k in ('sensing_horizon','horizontal_fov_deg')}
    gate={'trigger_x':c['formation_switch']['trigger_x'],
          'stage_require_all_past':['all_wmr_past_x' in s for s in c['experiment']['stages']],
          'stage_all_past_x':[s.get('all_wmr_past_x',0.) for s in c['experiment']['stages']]}
    parameter_ok=evidence.get('complete') is True and len(p)==15
    parameter_ok &= all(p.get(f'/drone_{i}_ego_planner_node')==expected and
                        p.get(f'/drone_{i}_pcl_render_node')==sensors for i in range(7))
    parameter_ok &= p.get('/formation_manager')==gate
    for kind,target in (('actual_parameters',expected),('actual_sensing_parameters',sensors)):
        parameter_ok &= all(all(k in target and v==target[k] for k,v in values.items())
                            for values in r[kind].values())
    parameter_ok &= all(k in gate and v==gate[k] for k,v in r['actual_exit_gate_parameters'].items())
    checks={
        'independent_complete_parameter_chain':bool(parameter_ok),
        'all_seven_finish':r['finished']==[True]*7,
        'all_four_switches':r['stages']==[count]*7 and r['switches']==count,
        'no_fatal_errors':not r['errors'],
        'center_clearance':len(r['minimum_obstacle_center_clearance_per_car'])==7 and min(r['minimum_obstacle_center_clearance_per_car'])>0,
        'mesh_footprint':r['mesh_footprint']['passed'] is True,
        'planar_motion':all(abs(z-c['experiment']['route'][0][2])<1e-6 for z in r['z_range']),
        'all_local_sensors':len(r['local_cloud_samples'])==7 and all(r['local_cloud_samples']),
        'map_and_visualization':r['visual_samples']>0 and r['global_cloud_points']>0 and r['visual_graph']['passed'],
        'clean_shutdown':r['launch_returncode']==0 and r['processes_started']==r['processes_finished_cleanly'] and r['processes_started']>0,
        'forest_crossings':all(s['central_band_obstacles']>0 and s['all_wmr_crossed_inside_forest'] for s in r['forest_evidence']),
        'two_columns':r['two_column_alignment_passed'] is True,
        'final_formation':r['final_formation']['passed'] is True,
        'stopped_execution':len(r['stopped_position_drift'])==7 and max(r['stopped_position_drift'])<1e-8,
    }
    files=('report.json','config.json','events.json','geometry.json','odometry.npz','independent_parameter_readback.json')
    result=dict(passed=all(checks.values()),checks=checks,original_recorder_passed=r['passed'],
                original_parameter_chain_passed=r['parameter_chain_passed'],
                original_sensor_response_ids=sorted(r['actual_sensing_parameters']),
                explanation='Original startup parameter RPC missed a response; independent live readback verifies every node. Original report unchanged.',
                input_sha256={name:digest(directory/name) for name in files})
    if not result['passed']:
        raise ValueError(f'Independent historical replay validation failed: {checks}')
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('directory',type=Path)
    args=ap.parse_args();result=validate(args.directory)
    (args.directory/'historical_replay_validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
