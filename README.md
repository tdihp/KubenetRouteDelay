# Kubenetroutedelay

A POC for monitoring and mitigating kubenet routetable update delay caused issues on node.

## The problem

Routetable used by kubenet can have a delay when getting applied. Node can be not prepared with the full set of pod CIDR routes even though the kubelet updates the node as "Ready".

Pods scheduled quickly onto the node will have brief problem of getting the replied IP packets due to the missing routes.

For cluster-autoscaling applications, this will be frequently hit.

## How this works

This application eventually adds a taint to the newly created nodes, and then remove the taint when a daeminset pod on the node determines a dns query is working.

To avoid confusions, this application adds a V1NodeCondition to the target nodes. The full workflow works as this:

1. Watcher observes node update
2. Watcher adds a "KubenetRouteDelay" condition to the node.
3. Watcher adds a taint to the node.
4. Probe tests on the node that the route table is now in place.
5. Probe updates the "KubenetRouteDelay" condition.
6. Watcher observes the update, removes the taint.

## Limitations

* Watching and applying the condition and taint will have a delay. In worst case scenario, it will miss to update a node in time, so a not ready node might be leaked into the wild. --> Ideally this can be fixed by a mutating webhook.
* No HA for the watcher.
