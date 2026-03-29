import React, { useState, useRef, useEffect } from 'react';
import { FolderTree, FileJson2, Activity, Plus, Save, Layers, Trash2, Download, Upload, RotateCcw } from 'lucide-react';

import type { IntentSchema, IntentSchemaOperation, IntentSchemaField } from '../types/intentSchema';
import IntentTreePanel from '../components/intent_parser/IntentTreePanel';
import IntentGraphCanvas from '../components/intent_parser/IntentGraphCanvas';
import { Link as LinkIcon } from 'lucide-react';
import { Toast } from '../src/utils/swal';

type ParserTab = 'tree' | 'category' | 'operation';
type ParserNodeSelection = { type: string; category?: string; operation?: string; parameter?: string } | null;
const STORAGE_KEY = 'intent-parser-required-only-schema-v1';

const initialMockSchema: IntentSchema = {
  target_devices: { required: true },
  categories: [
    {
      name: "INTERFACE",
      description: "Manage physical and logical network interfaces.",
      fields: [
        { name: "device", type: "string", required: true },
        { name: "interface_name", type: "string", required: true },
        { name: "description", type: "string", required: false },
        { name: "admin_state", type: "enum", options: ["up", "down"], required: false },
        { name: "interface_mode", type: "enum", options: ["l3", "access", "trunk", "hybrid"], required: false },
        { name: "access_vlan", type: "integer", required: false },
        { name: "allowed_vlans", type: "array", required: false },
        { name: "native_vlan", type: "integer", required: false },
        { name: "ip_address", type: "string", required: false },
        { name: "prefix_length", type: "integer", required: false },
        { name: "vrf", type: "string", required: false },
        { name: "mtu", type: "integer", required: false },
        { name: "speed", type: "string", required: false },
        { name: "duplex", type: "enum", options: ["auto", "full", "half"], required: false },
        { name: "channel_group", type: "integer", required: false },
        { name: "member_interfaces", type: "array", required: false }
      ],
      operations: [
        {
          name: "create_or_update_interface",
          required_fields: ["target_devices", "interface_name"],
          optional_fields: ["description", "admin_state", "interface_mode", "mtu", "speed", "duplex"],
          forbidden_fields: ["ip_address", "prefix_length", "vrf"],
          dependencies: [{ condition: "interface_mode must be set if access_vlan or allowed_vlans is used", action: "enforce" }]
        },
        {
          name: "set_interface_description",
          required_fields: ["target_devices", "interface_name", "description"],
          optional_fields: [],
        },
        {
          name: "set_interface_admin_state",
          required_fields: ["target_devices", "interface_name", "admin_state"],
          optional_fields: [],
        },
        {
          name: "assign_l3_address",
          required_fields: ["target_devices", "interface_name", "ip_address", "prefix_length"],
          optional_fields: ["vrf"],
          forbidden_fields: ["access_vlan", "allowed_vlans", "native_vlan"],
          dependencies: [{ condition: "interface_mode should be l3", action: "enforce" }]
        },
        {
          name: "configure_l2_access_port",
          required_fields: ["target_devices", "interface_name", "access_vlan"],
          optional_fields: [],
          dependencies: [{ condition: "interface_mode must be access", action: "enforce" }]
        },
        {
          name: "configure_l2_trunk_port",
          required_fields: ["target_devices", "interface_name", "allowed_vlans"],
          optional_fields: ["native_vlan"],
          dependencies: [{ condition: "interface_mode must be trunk", action: "enforce" }]
        },
        {
          name: "configure_port_channel",
          required_fields: ["target_devices", "channel_group", "member_interfaces"],
          optional_fields: [],
        }
      ]
    },
    {
      name: "VLAN",
      description: "Manage Virtual LAN (VLAN) configurations.",
      fields: [
        { name: "vlan_id", type: "integer", required: true },
        { name: "vlan_name", type: "string", required: false },
        { name: "state", type: "enum", options: ["present", "absent"], required: false },
        { name: "target_interfaces", type: "array", required: false },
        { name: "interface_mode", type: "enum", options: ["access", "trunk"], required: false },
        { name: "native_vlan", type: "integer", required: false },
        { name: "allowed_vlans", type: "array", required: false },
        { name: "description", type: "string", required: false }
      ],
      operations: [
        { name: "create_vlan", required_fields: ["vlan_id"], optional_fields: [] },
        { name: "rename_vlan", required_fields: ["vlan_id", "vlan_name"], optional_fields: [] },
        { name: "delete_vlan", required_fields: ["vlan_id"], optional_fields: [] },
        { 
          name: "assign_vlan_to_access_port", 
          required_fields: ["vlan_id", "target_interfaces"], 
          optional_fields: [],
          dependencies: [{ condition: "interface_mode must be access", action: "enforce" }]
        },
        {
          name: "configure_trunk_vlans",
          required_fields: ["target_interfaces", "allowed_vlans"],
          optional_fields: [],
          dependencies: [{ condition: "interface_mode must be trunk", action: "enforce" }]
        },
        { name: "remove_vlan_from_interface", required_fields: ["vlan_id", "target_interfaces"], optional_fields: [] }
      ]
    },
    {
      name: "ACL",
      description: "Access Control Lists (ACLs) for security policies.",
      fields: [
        { name: "acl_name", type: "string", required: true },
        { name: "acl_type", type: "enum", options: ["standard", "extended", "ipv6"], required: true },
        { name: "sequence", type: "integer", required: false },
        { name: "action", type: "enum", options: ["permit", "deny"], required: true },
        { name: "protocol", type: "string", required: false },
        { name: "src_ip", type: "string", required: false },
        { name: "src_wildcard", type: "string", required: false },
        { name: "dst_ip", type: "string", required: false },
        { name: "dst_wildcard", type: "string", required: false },
        { name: "src_port", type: "string", required: false },
        { name: "dst_port", type: "string", required: false },
        { name: "remark", type: "string", required: false },
        { name: "bind_interface", type: "string", required: false },
        { name: "bind_direction", type: "enum", options: ["in", "out"], required: false }
      ],
      operations: [
        { name: "create_acl", required_fields: ["acl_name", "acl_type"], optional_fields: [] },
        { name: "add_acl_rule", required_fields: ["acl_name", "sequence", "action"], optional_fields: [] },
        { name: "update_acl_rule", required_fields: ["acl_name", "sequence"], optional_fields: [] },
        { name: "delete_acl_rule", required_fields: ["acl_name", "sequence"], optional_fields: [] },
        { name: "apply_acl_to_interface", required_fields: ["acl_name", "bind_interface", "bind_direction"], optional_fields: [] },
        { name: "remove_acl_from_interface", required_fields: ["acl_name", "bind_interface", "bind_direction"], optional_fields: [] }
      ]
    },
    {
      name: "QOS",
      description: "Quality of Service (QoS) traffic policies.",
      fields: [
        { name: "policy_name", type: "string", required: true },
        { name: "class_name", type: "string", required: false },
        { name: "match_criteria", type: "string", required: false },
        { name: "action_type", type: "enum", options: ["bandwidth", "priority", "police", "shape", "queue"], required: false },
        { name: "bandwidth_percent", type: "integer", required: false },
        { name: "priority_level", type: "integer", required: false },
        { name: "police_rate", type: "string", required: false },
        { name: "shape_rate", type: "string", required: false },
        { name: "queue_limit", type: "integer", required: false },
        { name: "interface_name", type: "string", required: false },
        { name: "direction", type: "enum", options: ["in", "out"], required: false }
      ],
      operations: [
        { name: "create_qos_policy", required_fields: ["policy_name"], optional_fields: [] },
        { name: "add_qos_class", required_fields: ["policy_name", "class_name", "match_criteria"], optional_fields: [] },
        { name: "define_qos_action", required_fields: ["policy_name", "class_name", "action_type"], optional_fields: [] },
        { name: "apply_qos_policy_to_interface", required_fields: ["policy_name", "interface_name", "direction"], optional_fields: [] },
        { name: "update_qos_class", required_fields: ["policy_name", "class_name"], optional_fields: [] },
        { name: "remove_qos_policy_from_interface", required_fields: ["policy_name", "interface_name", "direction"], optional_fields: [] }
      ]
    },
    {
      name: "STATIC_ROUTE",
      description: "Static Routing path configurations.",
      fields: [
        { name: "vrf", type: "string", required: false },
        { name: "destination_network", type: "string", required: true },
        { name: "prefix_length", type: "integer", required: true },
        { name: "next_hop", type: "string", required: false },
        { name: "outgoing_interface", type: "string", required: false },
        { name: "administrative_distance", type: "integer", required: false },
        { name: "track_object", type: "string", required: false },
        { name: "route_tag", type: "string", required: false },
        { name: "description", type: "string", required: false }
      ],
      operations: [
        { name: "add_static_route", required_fields: ["destination_network", "prefix_length"], optional_fields: [] },
        { name: "delete_static_route", required_fields: ["destination_network", "prefix_length"], optional_fields: [] },
        { name: "update_static_route", required_fields: ["destination_network", "prefix_length"], optional_fields: [] },
        { name: "add_default_route", required_fields: ["next_hop"], optional_fields: [] },
        { name: "add_floating_static_route", required_fields: ["destination_network", "prefix_length", "next_hop", "administrative_distance"], optional_fields: [] }
      ]
    },
    {
      name: "OSPF",
      description: "OSPF dynamic routing protocol settings.",
      fields: [
        { name: "process_id", type: "integer", required: true },
        { name: "router_id", type: "string", required: false },
        { name: "area_id", type: "string", required: false },
        { name: "network", type: "string", required: false },
        { name: "wildcard_mask", type: "string", required: false },
        { name: "interface_name", type: "string", required: false },
        { name: "cost", type: "integer", required: false },
        { name: "hello_interval", type: "integer", required: false },
        { name: "dead_interval", type: "integer", required: false },
        { name: "passive_interface", type: "boolean", required: false },
        { name: "authentication", type: "enum", options: ["none", "simple", "md5"], required: false },
        { name: "neighbor_ip", type: "string", required: false }
      ],
      operations: [
        { name: "enable_ospf_process", required_fields: ["process_id"], optional_fields: [] },
        { name: "advertise_network", required_fields: ["process_id", "network", "wildcard_mask", "area_id"], optional_fields: [] },
        { name: "enable_ospf_on_interface", required_fields: ["process_id", "interface_name", "area_id"], optional_fields: [] },
        { name: "set_ospf_router_id", required_fields: ["process_id", "router_id"], optional_fields: [] },
        { name: "configure_passive_interface", required_fields: ["process_id", "interface_name", "passive_interface"], optional_fields: [] },
        { name: "remove_ospf_configuration", required_fields: ["process_id"], optional_fields: [] }
      ]
    }
  ]
};

function sanitizeSchema(raw: IntentSchema): IntentSchema {
  const categories = (raw.categories || []).map((category) => {
    const fields = (category.fields || [])
      .map((field) => ({
        ...field,
        name: String(field.name || '').trim(),
        type: String(field.type || 'string').trim() || 'string',
        required: !!field.required,
      }))
      .filter((field) => !!field.name);

    const knownFields = new Set(fields.map((field) => field.name));
    const operations = (category.operations || []).map((operation) => {
      const required = Array.from(
        new Set((operation.required_fields || [])
          .map((f) => String(f || '').trim())
          .filter((f) => !!f && (f === 'target_devices' || knownFields.has(f)))),
      );
      const dependencies = (operation.dependencies || [])
        .map((dep) => ({
          condition: String(dep.condition || '').trim(),
          action: String(dep.action || 'enforce').trim() || 'enforce',
        }))
        .filter((dep) => !!dep.condition);

      return {
        name: String(operation.name || '').trim() || `operation_${Date.now()}`,
        description: operation.description,
        required_fields: required,
        dependencies,
      } as IntentSchemaOperation;
    });

    return {
      ...category,
      name: String(category.name || '').trim() || 'CATEGORY',
      description: String(category.description || '').trim(),
      fields,
      operations,
    };
  });

  return {
    target_devices: { required: true },
    categories,
  };
}

const IntentParserView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<ParserTab>('tree');
  const [schema, setSchema] = useState<IntentSchema>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        return sanitizeSchema(JSON.parse(saved) as IntentSchema);
      }
    } catch {
      // Fall back to defaults when local data is invalid.
    }
    return sanitizeSchema(initialMockSchema);
  });
  const [selectedNode, setSelectedNode] = useState<ParserNodeSelection>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  
  // State for Category Tab
  const [selectedCategoryName, setSelectedCategoryName] = useState<string>(schema.categories[0]?.name || '');
  
  // State for Operation Tab
  const [selectedOperationCat, setSelectedOperationCat] = useState<string>(schema.categories[0]?.name || '');
  const [selectedOperationName, setSelectedOperationName] = useState<string>(schema.categories[0]?.operations[0]?.name || '');

  // Computed Context
  const selectedCategory = schema.categories.find(c => c.name === selectedCategoryName);
  const selectedOperationCategory = schema.categories.find(c => c.name === selectedOperationCat);
  const selectedOperation = selectedOperationCategory?.operations.find(o => o.name === selectedOperationName);

  useEffect(() => {
    if (!schema.categories.length) return;
    if (!schema.categories.some((c) => c.name === selectedCategoryName)) {
      setSelectedCategoryName(schema.categories[0].name);
    }
  }, [schema, selectedCategoryName]);

  useEffect(() => {
    if (!schema.categories.length) return;
    const opCat = schema.categories.find((c) => c.name === selectedOperationCat) || schema.categories[0];
    if (opCat.name !== selectedOperationCat) {
      setSelectedOperationCat(opCat.name);
      setSelectedOperationName(opCat.operations[0]?.name || '');
      return;
    }
    if (!opCat.operations.some((o) => o.name === selectedOperationName)) {
      setSelectedOperationName(opCat.operations[0]?.name || '');
    }
  }, [schema, selectedOperationCat, selectedOperationName]);

  const persistSchema = (nextSchema: IntentSchema, toastText: string) => {
    const cleaned = sanitizeSchema(nextSchema);
    setSchema(cleaned);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cleaned));
    Toast.success('Intent Parser', toastText);
  };

  const handleSaveAll = () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(schema));
    Toast.success('Intent Parser', 'Schema saved locally.');
  };

  const handleResetSchema = () => {
    const reset = sanitizeSchema(initialMockSchema);
    persistSchema(reset, 'Schema reset to local defaults.');
    setSelectedNode(null);
    setSelectedCategoryName(reset.categories[0]?.name || '');
    setSelectedOperationCat(reset.categories[0]?.name || '');
    setSelectedOperationName(reset.categories[0]?.operations[0]?.name || '');
  };

  const handleExportSchema = () => {
    const blob = new Blob([JSON.stringify(schema, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'intent_schema_required_only.json';
    a.click();
    URL.revokeObjectURL(url);
    Toast.success('Intent Parser', 'Schema exported to JSON.');
  };

  const handleImportSchema = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result || '{}')) as IntentSchema;
        const imported = sanitizeSchema(parsed);
        persistSchema(imported, 'Schema imported and sanitized to required-only mode.');
      } catch {
        Toast.error('Import failed', 'Invalid JSON file.');
      }
    };
    reader.readAsText(file);
    event.target.value = '';
  };

  // -- Mutations -- //
  const handleUpdateCategoryDescription = (desc: string) => {
    setSchema(prev => {
       const newSchema = { ...prev, categories: [...prev.categories] };
       const catIndex = newSchema.categories.findIndex(c => c.name === selectedCategoryName);
       if (catIndex >= 0) {
         newSchema.categories[catIndex] = { ...newSchema.categories[catIndex], description: desc };
       }
       return newSchema;
    });
  };

  const handleUpdateCategoryName = (newNameRaw: string) => {
    const newName = newNameRaw.trim().toUpperCase();
    if (!newName || !selectedCategoryName) return;
    setSchema((prev) => {
      const newSchema = { ...prev, categories: [...prev.categories] };
      const catIndex = newSchema.categories.findIndex((c) => c.name === selectedCategoryName);
      if (catIndex < 0) return prev;
      const duplicate = newSchema.categories.some((c, i) => i !== catIndex && c.name === newName);
      if (duplicate) {
        Toast.error('Category exists', `Category ${newName} already exists.`);
        return prev;
      }
      newSchema.categories[catIndex] = { ...newSchema.categories[catIndex], name: newName };
      return newSchema;
    });

    if (newName !== selectedCategoryName) {
      const oldName = selectedCategoryName;
      setSelectedCategoryName(newName);
      if (selectedOperationCat === oldName) {
        setSelectedOperationCat(newName);
      }
      setSelectedNode((prev) => {
        if (!prev) return prev;
        if (prev.category !== oldName) return prev;
        return { ...prev, category: newName };
      });
    }
  };

  const handleCreateCategory = () => {
    const suffix = Date.now().toString().slice(-4);
    const categoryName = `NEW_CATEGORY_${suffix}`;
    const operationName = `new_operation_${suffix}`;
    setSchema((prev) => {
      const newSchema = { ...prev, categories: [...prev.categories] };
      newSchema.categories.push({
        name: categoryName,
        description: '',
        fields: [
          { name: 'name', type: 'string', required: true },
        ],
        operations: [
          {
            name: operationName,
            required_fields: ['target_devices', 'name'],
            dependencies: [],
          },
        ],
      });
      return newSchema;
    });
    setSelectedCategoryName(categoryName);
    setSelectedOperationCat(categoryName);
    setSelectedOperationName(operationName);
    setActiveTab('category');
    Toast.success('Intent Parser', `Created ${categoryName}.`);
  };

  const handleDeleteCategory = () => {
    if (!selectedCategoryName) return;
    if (schema.categories.length <= 1) {
      Toast.warning('Cannot delete', 'At least one category is required.');
      return;
    }
    const ok = window.confirm(`Delete category ${selectedCategoryName}?`);
    if (!ok) return;

    setSchema((prev) => {
      const newSchema = { ...prev, categories: [...prev.categories] };
      const idx = newSchema.categories.findIndex((c) => c.name === selectedCategoryName);
      if (idx < 0) return prev;
      newSchema.categories.splice(idx, 1);
      return newSchema;
    });

    const fallback = schema.categories.find((c) => c.name !== selectedCategoryName)?.name || '';
    setSelectedCategoryName(fallback);
    setSelectedOperationCat(fallback);
    const fallbackCat = schema.categories.find((c) => c.name === fallback);
    setSelectedOperationName(fallbackCat?.operations[0]?.name || '');
    setSelectedNode(null);
    Toast.success('Intent Parser', 'Category deleted.');
  };

  const handleCreateField = () => {
    setSchema(prev => {
       const newSchema = { ...prev, categories: [...prev.categories] };
       const catIndex = newSchema.categories.findIndex(c => c.name === selectedCategoryName);
       if (catIndex >= 0) {
         const newField: IntentSchemaField = { name: `new_field_${Date.now().toString().slice(-4)}`, type: "string", required: false };
         newSchema.categories[catIndex] = { 
            ...newSchema.categories[catIndex], 
            fields: [...newSchema.categories[catIndex].fields, newField] 
         };
       }
       return newSchema;
    });
  };

  const handleUpdateField = (fieldIndex: number, fieldData: Partial<IntentSchemaField>) => {
    setSchema(prev => {
       const newSchema = { ...prev, categories: [...prev.categories] };
       const catIndex = newSchema.categories.findIndex(c => c.name === selectedCategoryName);
       if (catIndex >= 0) {
         const currentField = newSchema.categories[catIndex].fields[fieldIndex];
         const previousName = currentField?.name;
         const updatedFields = [...newSchema.categories[catIndex].fields];
         updatedFields[fieldIndex] = { ...updatedFields[fieldIndex], ...fieldData };
         const nextName = String(updatedFields[fieldIndex].name || '').trim();
         if (previousName && nextName && previousName !== nextName) {
           const updatedOps = newSchema.categories[catIndex].operations.map((op) => ({
             ...op,
             required_fields: (op.required_fields || []).map((f) => (f === previousName ? nextName : f)),
           }));
           newSchema.categories[catIndex] = { ...newSchema.categories[catIndex], fields: updatedFields, operations: updatedOps };
           return newSchema;
         }
         newSchema.categories[catIndex] = { ...newSchema.categories[catIndex], fields: updatedFields };
       }
       return newSchema;
    });
  };

  const handleRemoveField = (fieldIndex: number) => {
    setSchema(prev => {
       const newSchema = { ...prev, categories: [...prev.categories] };
       const catIndex = newSchema.categories.findIndex(c => c.name === selectedCategoryName);
       if (catIndex >= 0) {
         const updatedFields = [...newSchema.categories[catIndex].fields];
         const removedField = updatedFields[fieldIndex]?.name;
         updatedFields.splice(fieldIndex, 1);
         const updatedOps = (newSchema.categories[catIndex].operations || []).map((op) => ({
           ...op,
           required_fields: (op.required_fields || []).filter((f) => f !== removedField),
         }));
         newSchema.categories[catIndex] = { ...newSchema.categories[catIndex], fields: updatedFields, operations: updatedOps };
       }
       return newSchema;
    });
  };

  const handleCreateOperation = () => {
    const opName = `new_operation_${Date.now().toString().slice(-4)}`;
    setSchema(prev => {
       const newSchema = { ...prev, categories: [...prev.categories] };
       const catIndex = newSchema.categories.findIndex(c => c.name === selectedOperationCat);
       if (catIndex >= 0) {
         const newOp: IntentSchemaOperation = {
           name: opName,
           required_fields: [],
           dependencies: []
         };
         newSchema.categories[catIndex] = {
           ...newSchema.categories[catIndex],
           operations: [...newSchema.categories[catIndex].operations, newOp]
         };
       }
       return newSchema;
    });
    // Auto select the new operation
    setSelectedOperationName(opName);
  };

  const handleDeleteOperation = () => {
    setSchema((prev) => {
      const newSchema = { ...prev, categories: [...prev.categories] };
      const catIndex = newSchema.categories.findIndex((c) => c.name === selectedOperationCat);
      if (catIndex < 0) return prev;
      const ops = [...newSchema.categories[catIndex].operations];
      const opIndex = ops.findIndex((o) => o.name === selectedOperationName);
      if (opIndex < 0) return prev;
      ops.splice(opIndex, 1);
      newSchema.categories[catIndex] = { ...newSchema.categories[catIndex], operations: ops };
      setSelectedOperationName(ops[0]?.name || '');
      return newSchema;
    });
  };

  const handleUpdateOperationName = (newName: string) => {
    if (!newName.trim()) return;
    setSchema(prev => {
       const newSchema = { ...prev, categories: [...prev.categories] };
       const catIndex = newSchema.categories.findIndex(c => c.name === selectedOperationCat);
       if (catIndex >= 0) {
         const duplicate = newSchema.categories[catIndex].operations.find((o) => o.name === newName && o.name !== selectedOperationName);
         if (duplicate) return prev;
         const opIndex = newSchema.categories[catIndex].operations.findIndex(o => o.name === selectedOperationName);
         if (opIndex >= 0) {
            const updatedOps = [...newSchema.categories[catIndex].operations];
            updatedOps[opIndex] = { ...updatedOps[opIndex], name: newName };
            newSchema.categories[catIndex] = { ...newSchema.categories[catIndex], operations: updatedOps };
         }
       }
       return newSchema;
    });
    setSelectedOperationName(newName);
  };

  const handleToggleRequiredField = (fieldName: string) => {
    if (!selectedOperation) return;
    setSchema(prev => {
       const newSchema = { ...prev, categories: [...prev.categories] };
       const catIndex = newSchema.categories.findIndex(c => c.name === selectedOperationCat);
       if (catIndex >= 0) {
         const opIndex = newSchema.categories[catIndex].operations.findIndex(o => o.name === selectedOperationName);
         if (opIndex >= 0) {
             const op = newSchema.categories[catIndex].operations[opIndex];
             let reqs = [...op.required_fields];
             if (reqs.includes(fieldName)) {
                reqs = reqs.filter(f => f !== fieldName);
             } else {
                reqs.push(fieldName);
             }
             newSchema.categories[catIndex].operations[opIndex] = { ...op, required_fields: reqs.sort((a, b) => a.localeCompare(b)) };
         }
       }
       return newSchema;
    });
  };

  const handleAddDependency = () => {
    if (!selectedOperation) return;
    setSchema((prev) => {
      const newSchema = { ...prev, categories: [...prev.categories] };
      const catIndex = newSchema.categories.findIndex((c) => c.name === selectedOperationCat);
      if (catIndex < 0) return prev;
      const opIndex = newSchema.categories[catIndex].operations.findIndex((o) => o.name === selectedOperationName);
      if (opIndex < 0) return prev;
      const op = newSchema.categories[catIndex].operations[opIndex];
      const dependencies = [...(op.dependencies || []), { condition: '', action: 'enforce' }];
      newSchema.categories[catIndex].operations[opIndex] = { ...op, dependencies };
      return newSchema;
    });
  };

  const handleUpdateDependency = (depIndex: number, patch: { condition?: string; action?: string }) => {
    if (!selectedOperation) return;
    setSchema((prev) => {
      const newSchema = { ...prev, categories: [...prev.categories] };
      const catIndex = newSchema.categories.findIndex((c) => c.name === selectedOperationCat);
      if (catIndex < 0) return prev;
      const opIndex = newSchema.categories[catIndex].operations.findIndex((o) => o.name === selectedOperationName);
      if (opIndex < 0) return prev;
      const op = newSchema.categories[catIndex].operations[opIndex];
      const dependencies = [...(op.dependencies || [])];
      dependencies[depIndex] = { ...dependencies[depIndex], ...patch };
      newSchema.categories[catIndex].operations[opIndex] = { ...op, dependencies };
      return newSchema;
    });
  };

  const handleRemoveDependency = (depIndex: number) => {
    if (!selectedOperation) return;
    setSchema((prev) => {
      const newSchema = { ...prev, categories: [...prev.categories] };
      const catIndex = newSchema.categories.findIndex((c) => c.name === selectedOperationCat);
      if (catIndex < 0) return prev;
      const opIndex = newSchema.categories[catIndex].operations.findIndex((o) => o.name === selectedOperationName);
      if (opIndex < 0) return prev;
      const op = newSchema.categories[catIndex].operations[opIndex];
      const dependencies = [...(op.dependencies || [])];
      dependencies.splice(depIndex, 1);
      newSchema.categories[catIndex].operations[opIndex] = { ...op, dependencies };
      return newSchema;
    });
  };

  const handleSelectNode = (node: { type: string; category?: string; operation?: string; parameter?: string }) => {
    setSelectedNode(node);
    if (node.category) {
      setSelectedCategoryName(node.category);
      setSelectedOperationCat(node.category);
    }
    if (node.operation) {
      setSelectedOperationName(node.operation);
      setActiveTab('operation');
    } else if (node.category) {
      setActiveTab('category');
    }
  };

  // Sidebar Resizing State
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const isResizing = useRef(false);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return;
      setSidebarWidth(w => Math.max(200, Math.min(800, w + e.movementX)));
    };
    const handleMouseUp = () => {
      if (isResizing.current) {
        isResizing.current = false;
        document.body.style.cursor = 'default';
        document.body.style.userSelect = 'auto';
      }
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  return (
    <div className="flex flex-col h-full bg-base-100">
      {/* Top Navigation / Tabs */}
      <div className="border-b border-base-300 p-3 shrink-0 flex items-center justify-between shadow-sm z-20 relative">
        <h1 className="text-xl font-bold flex rounded items-center gap-2">
          <div className="bg-primary/20 p-1.5 rounded-md text-primary flex items-center justify-center">
             <FileJson2 className="w-5 h-5" />
          </div>
          Intent Schema Parser
        </h1>
        <div className="flex items-center gap-2">
          <div className="tabs tabs-boxed bg-base-200/50 p-1">
            <button 
              className={`tab tab-sm gap-2 transition-all ${activeTab === 'tree' ? 'tab-active bg-base-100 shadow-sm' : ''}`}
              onClick={() => setActiveTab('tree')}
            >
              <FolderTree className="w-3.5 h-3.5" />
              Overall Parse Tree
            </button>
            <button 
              className={`tab tab-sm gap-2 transition-all ${activeTab === 'category' ? 'tab-active bg-base-100 shadow-sm' : ''}`}
              onClick={() => setActiveTab('category')}
            >
              <Layers className="w-3.5 h-3.5" />
              Category Schema
            </button>
            <button 
              className={`tab tab-sm gap-2 transition-all ${activeTab === 'operation' ? 'tab-active bg-base-100 shadow-sm' : ''}`}
              onClick={() => setActiveTab('operation')}
            >
              <Activity className="w-3.5 h-3.5" />
              Operation Schema
            </button>
          </div>
          <button className="btn btn-sm btn-outline" onClick={handleSaveAll}><Save className="w-4 h-4" /> Save</button>
          <button className="btn btn-sm btn-outline" onClick={handleExportSchema}><Download className="w-4 h-4" /> Export</button>
          <button className="btn btn-sm btn-outline" onClick={() => fileInputRef.current?.click()}><Upload className="w-4 h-4" /> Import</button>
          <button className="btn btn-sm btn-outline" onClick={handleResetSchema}><RotateCcw className="w-4 h-4" /> Reset</button>
          <input ref={fileInputRef} type="file" accept="application/json" className="hidden" onChange={handleImportSchema} />
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-auto bg-base-200 p-4">
        
        {/* TAB 1: OVERALL INTENT PARSING TREE */}
        {activeTab === 'tree' && (
           <div className="flex gap-4 h-full">
              {/* Left sidebar tree view */}
              <div 
                 className="relative bg-base-100 p-3 rounded-l-lg shadow-sm border-y border-l border-base-300 overflow-y-auto shrink-0"
                 style={{ width: `${sidebarWidth}px` }}
              >
                 <h3 className="text-xs font-bold uppercase tracking-wider mb-3 opacity-70">Schema Hierarchy</h3>
                 <IntentTreePanel 
                    schema={schema} 
                    selectedNode={selectedNode} 
                    onSelectNode={handleSelectNode} 
                    onNavigateCategory={(cat) => {
                       setActiveTab('category');
                       setSelectedCategoryName(cat);
                    }}
                    onNavigateOperation={(cat, op) => {
                       setActiveTab('operation');
                       setSelectedOperationCat(cat);
                       setSelectedOperationName(op);
                    }}
                 />

                 {/* Custom Resizer Handle on the Right Vertical Edge */}
                 <div 
                    className="absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-base-content/10 bg-base-300 transition-colors z-10"
                    onMouseDown={() => {
                       isResizing.current = true;
                       document.body.style.cursor = 'col-resize';
                       document.body.style.userSelect = 'none'; // prevent text selection while dragging
                    }}
                 />
              </div>

              {/* Center graph canvas */}
              <div className="flex-1 bg-base-100 p-4 rounded-r-lg shadow-sm border-y border-r border-base-300 flex flex-col min-w-[400px]">
                 <h2 className="text-lg font-bold mb-1">Parser Dependency Graph</h2>
                 <p className="opacity-70 text-xs mb-4">Visual map of all configured network intent operations.</p>
                 <div className="flex-1 border border-base-200 rounded-md overflow-hidden relative min-h-[400px]">
                    <IntentGraphCanvas 
                       schema={schema} 
                       onNavigateCategory={(cat) => {
                          setActiveTab('category');
                          setSelectedCategoryName(cat);
                       }}
                       onNavigateOperation={(cat, op) => {
                          setActiveTab('operation');
                          setSelectedOperationCat(cat);
                          setSelectedOperationName(op);
                       }}
                    />
                 </div>
              </div>
           </div>
        )}

        {/* TAB 2: CATEGORY SPECIFIC INTENT SCHEMA */}
        {activeTab === 'category' && (
           <div className="bg-base-100 p-5 rounded-lg shadow-sm border border-base-300 max-w-5xl mx-auto flex flex-col gap-4">
              <div className="flex justify-between items-center pb-2 border-b border-base-200">
                 <div>
                   <h2 className="text-lg font-bold">Category Specific Intent Schema</h2>
                   <p className="opacity-70 text-xs">Define shared category fields and their generic data types.</p>
                 </div>
                 <div className="flex items-center gap-2">
                   <select 
                      className="select select-bordered select-sm w-48 text-sm font-semibold"
                      value={selectedCategoryName}
                      onChange={(e) => {
                        const next = e.target.value;
                        setSelectedCategoryName(next);
                        setSelectedOperationCat(next);
                        const cat = schema.categories.find((c) => c.name === next);
                        setSelectedOperationName(cat?.operations[0]?.name || '');
                      }}
                   >
                    {schema.categories.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                   </select>
                   <button className="btn btn-sm btn-outline btn-secondary" onClick={handleCreateCategory} title="Create Category">
                     <Plus className="w-4 h-4" />
                   </button>
                   <button className="btn btn-sm btn-outline btn-error" onClick={handleDeleteCategory} title="Delete Category">
                     <Trash2 className="w-4 h-4" />
                   </button>
                 </div>
              </div>

              {selectedCategory && (
                <div className="flex flex-col gap-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="form-control">
                      <label className="label py-1"><span className="label-text font-bold text-xs uppercase tracking-wide opacity-80">Category Name</span></label>
                      <input
                        type="text"
                        className="input input-sm input-bordered font-semibold"
                        value={selectedCategory.name}
                        onChange={(e) => handleUpdateCategoryName(e.target.value)}
                      />
                    </div>
                    <div className="form-control">
                      <label className="label py-1"><span className="label-text font-bold text-xs uppercase tracking-wide opacity-80">Category Description</span></label>
                      <input 
                         type="text" 
                         className="input input-sm input-bordered" 
                         value={selectedCategory.description || ''} 
                         onChange={(e) => handleUpdateCategoryDescription(e.target.value)} 
                      />
                    </div>
                  </div>

                  <div className="form-control mt-2">
                    <label className="label py-1"><span className="label-text font-bold text-xs uppercase tracking-wide opacity-80">Available Schema Fields</span></label>
                    <div className="bg-base-200/50 p-2 rounded-lg border border-base-200 max-h-[500px] overflow-y-auto">
                       <table className="table table-sm table-pin-rows">
                         <thead>
                            <tr>
                               <th>Field Name</th>
                               <th>Data Type</th>
                               <th>Requirement</th>
                               <th className="text-right">Actions</th>
                            </tr>
                         </thead>
                         <tbody>
                           {selectedCategory.fields.map((f, i) => (
                             <tr key={i} className="hover:bg-base-100">
                                <td>
                                  <input 
                                     type="text" 
                                     className="input input-xs input-bordered w-full font-mono font-semibold" 
                                     value={f.name}
                                     onChange={(e) => handleUpdateField(i, { name: e.target.value })}
                                  />
                                </td>
                                <td>
                                  <select 
                                     className="select select-xs select-bordered w-full" 
                                     value={f.type}
                                     onChange={(e) => handleUpdateField(i, { type: e.target.value })}
                                  >
                                     <option value="string">String</option>
                                     <option value="integer">Integer</option>
                                     <option value="boolean">Boolean</option>
                                     <option value="array">Array</option>
                                     <option value="enum">Enum</option>
                                  </select>
                                </td>
                                <td>
                                  <label className="label cursor-pointer justify-start gap-2 py-0">
                                     <input 
                                        type="checkbox" 
                                        className="checkbox checkbox-xs" 
                                        checked={f.required} 
                                        onChange={(e) => handleUpdateField(i, { required: e.target.checked })}
                                     />
                                     <span className="label-text text-xs">Always Required</span>
                                  </label>
                                </td>
                                <td className="text-right">
                                  <button 
                                     className="btn btn-ghost btn-xs text-error"
                                     onClick={() => handleRemoveField(i)}
                                     title="Remove Field"
                                  >
                                     <Trash2 className="w-3.5 h-3.5" />
                                  </button>
                                </td>
                             </tr>
                           ))}
                         </tbody>
                       </table>
                       
                       <div className="p-2 border-t border-base-300 mt-2">
                         <button className="btn btn-primary btn-sm btn-outline btn-block border-dashed" onClick={handleCreateField}>
                           <Plus className="w-4 h-4" /> Add Schema Field
                         </button>
                       </div>
                    </div>
                  </div>
                  
                  <div className="flex justify-end pt-2 border-t border-base-200">
                    <button className="btn btn-primary btn-sm" onClick={handleSaveAll}><Save className="w-4 h-4"/> Commit Changes</button>
                  </div>
                </div>
              )}
           </div>
        )}

        {/* TAB 3: OPERATION SPECIFIC INTENT SCHEMA */}
        {activeTab === 'operation' && (
           <div className="bg-base-100 p-5 rounded-lg shadow-sm border border-base-300 max-w-5xl mx-auto flex flex-col gap-4">
              <div className="flex justify-between items-center pb-2 border-b border-base-200">
                 <div>
                   <h2 className="text-lg font-bold">Operation Specific Intent Schema</h2>
                   <p className="opacity-70 text-xs">Configure required parameters and strict dependencies for specific operations.</p>
                 </div>
                 <div className="flex gap-2 items-center">
                     <select 
                        className="select select-bordered select-sm w-36 text-sm"
                        value={selectedOperationCat}
                        onChange={(e) => {
                           setSelectedOperationCat(e.target.value);
                           const cat = schema.categories.find(c => c.name === e.target.value);
                           if (cat && cat.operations.length > 0) {
                              setSelectedOperationName(cat.operations[0].name);
                           } else {
                              setSelectedOperationName("");
                           }
                        }}
                     >
                        {schema.categories.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                     </select>
                     
                     {selectedOperationCategory?.operations && selectedOperationCategory.operations.length > 0 ? (
                       <select 
                          className="select select-bordered select-sm select-secondary min-w-[180px] font-semibold text-sm"
                          value={selectedOperationName}
                          onChange={(e) => setSelectedOperationName(e.target.value)}
                       >
                          {selectedOperationCategory.operations.map(o => <option key={o.name} value={o.name}>{o.name}</option>)}
                       </select>
                     ) : (
                       <span className="opacity-50 italic text-xs px-2">No Operations</span>
                     )}

                     <button className="btn btn-square btn-outline btn-secondary btn-sm tooltip" data-tip="Create New Operation" onClick={handleCreateOperation}>
                        <Plus className="w-4 h-4" />
                     </button>
              <button className="btn btn-square btn-outline btn-error btn-sm tooltip" data-tip="Delete Operation" onClick={handleDeleteOperation} disabled={!selectedOperation}>
                <Trash2 className="w-4 h-4" />
              </button>
                 </div>
              </div>

              {selectedOperation && (
                <div className="flex flex-col gap-5">
                  
                  <div className="p-3 bg-secondary/5 border border-secondary/20 rounded-md flex gap-4 items-center">
                     <div className="flex gap-1 items-center text-xs font-bold uppercase tracking-wider opacity-70">
                        <Activity className="w-3.5 h-3.5 text-secondary" /> Identifier
                     </div>
                     <input 
                        type="text" 
                        className="input input-sm border-secondary/40 font-bold font-mono text-secondary bg-base-100 flex-1 max-w-md" 
                        value={selectedOperation.name}
                        onChange={(e) => handleUpdateOperationName(e.target.value)} 
                     />
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="form-control md:col-span-2">
                       <label className="label py-1"><span className="label-text font-bold text-error text-xs uppercase tracking-wide">Required Fields</span></label>
                       <p className="text-[10px] opacity-60 mb-2 leading-tight">Click fields below to toggle their required status for this specific operation.</p>
                       <div className="bg-error/5 p-3 rounded-md flex flex-wrap gap-2 border border-error/20 min-h-20 content-start">
                          {selectedOperation.required_fields.map(f => (
                            <button 
                               key={f} 
                               className="badge badge-error gap-1 p-2 font-mono text-xs cursor-pointer hover:badge-outline transition-all"
                               onClick={() => handleToggleRequiredField(f)}
                            >
                              {f} <span className="opacity-60 hover:opacity-100 ml-1 font-bold">×</span>
                            </button>
                          ))}
                          {selectedOperation.required_fields.length === 0 && <span className="text-xs opacity-50 italic mt-1">None specified.</span>}
                       </div>
                       <select 
                          className="select select-bordered select-xs w-full mt-2"
                          value="default"
                          onChange={(e) => {
                            if (e.target.value !== 'default') {
                              handleToggleRequiredField(e.target.value);
                            }
                          }}
                       >
                          <option value="default" disabled>+ Assign existing category field</option>
                          {selectedOperationCategory?.fields.filter(f => !selectedOperation.required_fields.includes(f.name)).map(f => (
                             <option key={f.name} value={f.name}>{f.name}</option>
                          ))}
                       </select>
                     </div>
                  </div>

                  <div className="form-control mt-2 pt-4 border-t border-base-200">
                    <label className="label py-1"><span className="label-text font-bold text-info flex gap-2 items-center text-xs uppercase tracking-wide">Dependencies</span></label>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-2">
                       {selectedOperation.dependencies?.map((d, i) => (
                         <div key={i} className="flex gap-2 items-center bg-info/5 p-2 rounded border border-info/20 text-xs shadow-sm">
                            <LinkIcon className="w-3.5 h-3.5 text-info shrink-0" />
                            <input
                              className="input input-xs input-bordered flex-1 font-mono"
                              value={d.condition}
                              placeholder="condition"
                              onChange={(e) => handleUpdateDependency(i, { condition: e.target.value })}
                            />
                            <select
                              className="select select-xs select-bordered"
                              value={d.action}
                              onChange={(e) => handleUpdateDependency(i, { action: e.target.value })}
                            >
                              <option value="enforce">enforce</option>
                              <option value="warn">warn</option>
                              <option value="derive">derive</option>
                            </select>
                            <button className="btn btn-ghost btn-xs text-error" onClick={() => handleRemoveDependency(i)}>
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                         </div>
                       ))}
                       <button className="btn btn-outline btn-info btn-sm border-dashed h-full min-h-12 w-full justify-center" onClick={handleAddDependency}>
                         <Plus className="w-3.5 h-3.5" /> Append Dependency Rule
                       </button>
                    </div>
                  </div>

                  <div className="rounded-md border border-base-300 bg-base-200/40 p-2 text-xs opacity-70">
                    Required-only mode is enabled. Optional and restricted fields are intentionally hidden and not edited here.
                  </div>

                  <div className="flex justify-end pt-2 border-t border-base-200">
                     <button className="btn btn-secondary btn-sm" onClick={handleSaveAll}><Save className="w-4 h-4"/> Commit Operation</button>
                  </div>
                </div>
              )}
           </div>
        )}

      </div>
    </div>
  );
};

export default IntentParserView;
