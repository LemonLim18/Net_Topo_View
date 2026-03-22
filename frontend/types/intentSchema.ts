export interface IntentSchemaField {
  name: string;
  type: string;
  required: boolean;
  description?: string;
  options?: string[];
  validation?: string;
}

export interface IntentSchemaDependency {
  condition: string;
  action: string;
}

export interface IntentSchemaOperation {
  name: string;
  description?: string;
  required_fields: string[];
  optional_fields: string[];
  forbidden_fields?: string[];
  dependencies?: IntentSchemaDependency[];
  cross_check_rules?: string[];
}

export interface IntentSchemaCategory {
  name: string;
  description?: string;
  fields: IntentSchemaField[];
  operations: IntentSchemaOperation[];
}

export interface IntentSchema {
  target_devices: {
    required: boolean;
  };
  categories: IntentSchemaCategory[];
}
