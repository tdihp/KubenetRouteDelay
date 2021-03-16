""" main application

"""

import logging
import argparse
import time
import math
import socket
import sys
import os

from .k8s import node_watch_loop, TaintManager, ConditionManager, TaintAndConditionUpdater, set_condition_by_node_name

LOG_FORMAT = '%(asctime)-15s %(levelname)s %(name)s - %(message)s'
logger = logging.getLogger('kubenetroutedelay')


def watcher(node_selector):
    taint_manager = TaintManager(key='kubenetroutedelay', value='yes', effect='NoSchedule')
    condition_manager = ConditionManager(type='KubenetRouteDelay', status='True', reason='PendingDelay', message='pending daemonset update')
    node_updater = TaintAndConditionUpdater(
        taint_manager=taint_manager,
        condition_manager=condition_manager,
    )
    node_watch_loop(node_updater, node_selector)


def exp_step(min_v, max_v, steps=20):
    assert min_v > 0
    assert max_v > min_v
    assert steps > 1
    diff = math.log(max_v) - math.log(min_v)
    stepv = diff / steps
    x = min_v
    for i in range(steps):
        yield min_v * math.exp(i * stepv)

    while True:
        yield max_v


def probe(node_name, name_to_test):
    condition_manager = ConditionManager(type='KubenetRouteDelay', status='False', reason='ProbePassed', message='daemonset probe passed')
    sleep_step = exp_step(1, 300, 20)
    while True:
        logger.info('attempting to resolve %s', name_to_test)
        try:
            socket.gethostbyname(name_to_test)
        except Exception as e:
            logger.info('failed to resolve: %s', e)
        else:
            logger.info('attempt successful, setting condition')
            set_condition_by_node_name(node_name, condition_manager)
            logger.info('condition done, sleep forever')
            break
        time.sleep(next(sleep_step))

    while True:
        time.sleep(3600)


def main():
    """
    For probe, it takes node name from the env variable

    """
    from kubernetes import config
    
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--kube-config', action='store_true', help='use kube-config, by default we use in-cluster config')
    parser.add_argument('-n', '--probe-node-name', help='nodename to probe')
    parser.add_argument('--probe-domain-name', default='kubernetes.default', help='dns query for testing readiness (default: kubernetes.default)')
    parser.add_argument('-s', '--watcher-node-selector', default='beta.kubernetes.io/os=linux,kubernetes.azure.com/mode=user')
    parser.add_argument('-v', '--verbose', action='store_true', help='show verbose logs')
    parser.add_argument('command', choices=('watcher', 'probe'))

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format=LOG_FORMAT)

    if args.kube_config:
        config.load_kube_config()
    else:
        config.load_incluster_config()

    if args.command == 'probe':
        if not args.probe_node_name:
            raise KeyError('Node name missing for probe')

        probe(args.probe_node_name, args.probe_domain_name)

    else:
        assert args.command == 'watcher'
        watcher(args.watcher_node_selector)


if __name__ == '__main__':
    main()
