
import type { TopologyData } from './types';

export const topologyData: TopologyData = {
  "timestamp": "2025-11-14T13:49:50+08:00",
  "seeds": [
    "192.168.1.123"
  ],
  "nodes": {
    "R1": {
      "interfaceList": [
        {
          "Ethernet0/1": "192.168.1.123",
          "Ethernet0/2": "192.168.2.1",
          "Ethernet0/3": "192.168.5.1",
          "Loopback1": "10.0.0.1"
        }
      ],
      "connectedNode": [
        "R3",
        "R2"
      ],
      "edgeLink": {
        "Et0/3": {
          "remote_node": "R3",
          "remote_interface": "Et0/3",
          "remote_ip": "192.168.5.3"
        },
        "Et0/2": {
          "remote_node": "R2",
          "remote_interface": "Et0/3",
          "remote_ip": "192.168.2.2"
        }
      },
      "mgmt_ip": "10.0.0.1"
    },
    "R3": {
      "interfaceList": [
        {
          "Ethernet0/2": "192.168.4.3",
          "Ethernet0/3": "192.168.5.3",
          "Loopback1": "10.0.0.3"
        }
      ],
      "connectedNode": [
        "R1",
        "R4"
      ],
      "edgeLink": {
        "Et0/3": {
          "remote_node": "R1",
          "remote_interface": "Et0/3",
          "remote_ip": "192.168.5.1"
        },
        "Et0/2": {
          "remote_node": "R4",
          "remote_interface": "Et0/2",
          "remote_ip": "192.168.4.4"
        }
      },
      "mgmt_ip": "10.0.0.3"
    },
    "R2": {
      "interfaceList": [
        {
          "Ethernet0/1": "192.168.3.2",
          "Ethernet0/2": "192.168.6.1",
          "Ethernet0/3": "192.168.2.2",
          "Loopback1": "10.0.0.2"
        }
      ],
      "connectedNode": [
        "R1",
        "R4"
      ],
      "edgeLink": {
        "Et0/3": {
          "remote_node": "R1",
          "remote_interface": "Et0/2",
          "remote_ip": "192.168.2.1"
        },
        "Et0/1": {
          "remote_node": "R4",
          "remote_interface": "Et0/1",
          "remote_ip": "192.168.3.4"
        }
      },
      "mgmt_ip": "10.0.0.2"
    },
    "R4": {
      "interfaceList": [
        {
          "Ethernet0/0": "192.168.7.1",
          "Ethernet0/1": "192.168.3.4",
          "Ethernet0/2": "192.168.4.4",
          "Loopback1": "10.0.0.4"
        }
      ],
      "connectedNode": [
        "R2",
        "R3"
      ],
      "edgeLink": {
        "Et0/2": {
          "remote_node": "R3",
          "remote_interface": "Et0/2",
          "remote_ip": "192.168.4.3"
        },
        "Et0/1": {
          "remote_node": "R2",
          "remote_interface": "Et0/1",
          "remote_ip": "192.168.3.2"
        }
      },
      "mgmt_ip": "10.0.0.4"
    }
  }
};
