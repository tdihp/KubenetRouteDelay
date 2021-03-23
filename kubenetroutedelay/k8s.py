"""
All stuff interacting with Kubernetes
"""
from datetime import datetime
import logging
from collections import namedtuple
import socket

from dateutil.tz import tzutc
from urllib3.connection import HTTPConnection

from kubernetes import client, watch

logger = logging.getLogger('k8s')


def set_condition(node, type, status, reason, message):
    """set condition of condition_name for the node"""
    v1 = client.CoreV1Api()
    node_name = node.metadata.name
    
    original_condition = None
    original_conditions = [condition for condition in node.status.conditions if condition.type == type]
    if original_conditions:
        assert len(original_conditions) == 1
        original_condition, = original_conditions

    now = datetime.now(tz=tzutc())
    last_transition_time = now
    if original_condition and original_condition.status == status:
        last_transition_time = original_condition.last_transition_time

    condition = client.V1NodeCondition(
        type=type,
        status=status,
        reason=reason,
        message=message,
        last_heartbeat_time=now,
        last_transition_time=last_transition_time,
    )

    patch = {'status': {'conditions': [condition]}}
    rtn = v1.patch_node_status(node_name, patch)
    return rtn


def get_condition_status(node, type):
    """ extract condition info from node object, only status is returned, None if type not found """
    for condition in node.status.conditions:
        if condition.type == type:
            return condition.status

    return None


def taint_node(node, key, value, effect='NoSchedule'):
    node_name = node.metadata.name
    taints = node.spec.taints or []
    assert not any(taint.key == key for taint in taints)
    taint = client.V1Taint(
        key=key,
        effect=effect,
        time_added=datetime.now(tz=tzutc()),
    )
    taints.append(taint)
    patch = {'spec': {'taints': taints}}
    v1 = client.CoreV1Api()
    rtn = v1.patch_node(node_name, patch)
    return rtn


def untaint_node(node, key):
    node_name = node.metadata.name
    taints = node.spec.taints or []
    assert any(taint.key == key for taint in taints)
    taints = [taint for taint in taints if taint.key != key]
    patch = {'spec': {'taints': taints}}
    v1 = client.CoreV1Api()
    rtn = v1.patch_node(node_name, patch)
    return rtn


def node_has_taint(node, key):
    for taint in (node.spec.taints or []):
        if taint.key == key:
            return True

    return False


class TaintManager(namedtuple('TaintManager', ['key', 'value', 'effect'])):
    __slots__ = ()

    def node_has_taint(self, node):
        return node_has_taint(node, self.key)

    def taint_node(self, node):
        return taint_node(node, **self._asdict())

    def untaint_node(self, node):
        return untaint_node(node, self.key)


class ConditionManager(namedtuple('ConditionManager', ['type', 'status', 'reason', 'message'])):
    __slots__ = ()

    def get_condition_status(self, node):
        return get_condition_status(node, self.type)
    
    def set_condition(self, node):
        set_condition(node, **self._asdict())

    def condition_status_equals(self, node):
        status = self.get_condition_status(node)
        if status is None:
            return status

        return status == self.status


class NodeUpdater(object):
    def update(self, node):
        raise NotImplementedError


class TaintAndConditionUpdater(NodeUpdater):
    def __init__(self, taint_manager, condition_manager):
        self.taint_manager = taint_manager
        self.condition_manager = condition_manager

    def update(self, node):
        node_name = node.metadata.name
        has_taint = self.taint_manager.node_has_taint(node)
        condition_status_same = self.condition_manager.condition_status_equals(node)
        need_taint = False
        if condition_status_same is None:
            logger.info('set condition for node %s', node_name)
            need_taint = True
            self.condition_manager.set_condition(node)
            logger.info('condition set done')

        elif condition_status_same:
            need_taint = True

        if need_taint and not has_taint:
            logger.info('taint node %s', node_name)
            self.taint_manager.taint_node(node)
            logger.info('taint done')

        elif not need_taint and has_taint:
            logger.info('untaint node %s', node_name)
            self.taint_manager.untaint_node(node)
            logger.info('untaint done')

        logger.info('update node done')


class ConditionUpdater(NodeUpdater):
    def __init__(self, condition_manager):
        self.condition_manager = condition_manager
    
    def update(self, node):
        condition_status_same = self.condition_manager.condition_status_equals(node)
        if condition_status_same is None or not condition_status_same:
            logger.info('set condition for node %s', node_name)
            self.condition_manager.set_condition(node)
            logger.info('condition set done')


def node_watch_loop(
        # pool,
        node_updater,
        label_selector,
        triggered_events = ('ADDED', 'MODIFIED'),
        ):
    v1 = client.CoreV1Api()
    # hack: keep alive https://github.com/kubernetes-client/python/issues/1234#issuecomment-695801558
    socket_options = HTTPConnection.default_socket_options + [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5),
        (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 30),
        (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3),
    ]
    v1.api_client.rest_client.pool_manager.connection_pool_kw['socket_options'] = socket_options

    watcher = watch.Watch()
    for event in watcher.stream(v1.list_node, label_selector=label_selector):
        event_type = event['type']
        node = event['object']
        node_name = node.metadata.name
        logger.debug('event: %s %s', event_type, node_name)
        if event_type in triggered_events:
            logger.info('update node %s', node_name)
            node_updater.update(node)

    logger.info('watch loop finished')


def set_condition_by_node_name(node_name, condition_manager):
    v1 = client.CoreV1Api()
    node = v1.read_node(node_name)
    return condition_manager.set_condition(node)


def main():
    import logging
    logging.basicConfig(level=logging.DEBUG)
    pool = gevent.pool.Pool(5)

    taint_manager = TaintManager(key='kubenetroutedelay', value='yes', effect='NoSchedule')
    condition_manager = ConditionManager(type='KubenetRouteDelay', status='True', reason='PendingDelay', message='pending daemonset update')
    node_updater = TaintAndConditionUpdater(
        taint_manager=taint_manager,
        condition_manager=condition_manager,
    )
    node_watch_loop(node_updater, 'agentpool=nodepool1')

