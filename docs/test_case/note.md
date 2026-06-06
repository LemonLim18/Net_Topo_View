What have been done right now:
1. Create an Intent Parsing Tree/Parameter Dependency Graph: Each operation under the same categories in the tree(topmost node: device(s)) share the same intent tree(with same leaf or same field) but different intent schemas(some fields are required to one operation, but are optional to the others). This is being implemented right now using the files: intent_operation_reference.md(Narrative) and under the folder backend/ansible/intent_schema/*.yml as the rules. Both will be updated at the same time upon every user prompt in the Cursor IDE.
2. Trying to make the system LLM not to classify the LLM prompt as single one schema actions, but can contain multiple actions in one prompt. For example, create VLAN 100 named HR, and configure the Gigabitethernet1/0/1 as the access, isnt just 'Configure interface' action, thats why leading to using the create interface schema, but it also uses the "Create vlan" yaml. The next time, it should use "Create VLAN" and "Configuring Interface"  schema actions. Same applies to other categories(not just vlan, and interface) in the future when deploying.


TODO-LIST:
1. Remember to add the dropdown list for selection as the addition of new entities/entries inside the preview modal dialog. This information can be retrieved from the planned network context.
2. As well inside the preview modal, only the parameters in that categories-specific intent tree(VLAN) (which all the operations under the same categories(VLAN) would share the same field attributes/parameters) will be shown/the other categories(e.g ACL, static route, etc)will be hidden. While the operation-specific schema for each operation under the same category would determine which fields are required and which are considered as optional for that particular operation.
3. Remember to figure out why the recommended device option list includes the unexpected awe and dsa. Is it stored inside the hidden local DB?
4. The active file being opened right now, will cause the movement of tab to the viewport of the user. [Done]
5. Make the Ansible button stop button round abit and avoid showing the red [Done]
6. For the Automation section, there create another tab which is the Intent Parsing Tree/Parameter Dependency Graph, which contains from the device to the categories to operation-specific intent schema. It becomes like the Netbox, where I can create the operation-specific intent schema, create fields for the Categories-Specific Intent Schema, set what fields to be required for what Operation-Specific Intent Schema. The whole view will be like the Netbox and the form. Different View for different categories, each categories contains more sub-views which share the same field list as another categories. All these operation-specific categories will be used for the LLM Intent Parser. The admin can use this view to update the "fill-in form" to be cross-checked against the user prompt [Critical]

For example:
Format:
1. {
  "intent": "configure_vlan",
  "device": "",
  "parameters": {
    "vlan_id": ,
    "vlan_name":,
  }
}

Dependencyg
2. required_params = {
  "configure_vlan": ["vlan_id", "vlan_name"]
}


AI Prompt Rule:
1. Before implementing the fix, please determine if the fix is case-specific or generic to all other operations/categories, thereby apply the fix for other all operations or categories for the parts which are general and similar, to reduce the repeated fix for other categories/operations in the future.
2. If there's a clashing on how the backend operates for each operation under the same category after the programmer is asking the AI to solve the next operations from either the same category or different categories that may cause the disruption or failure to the previous operation prompt, then remember to bring this to the awareness of the programmer before you implement the fix.
3.  If the operation involves or requires the modification/update @backend/ansible/intent_schemas, please update it  as well as updating the corresponding update info in here@docs/intent_operation_reference.md. The @backend/ansible/intent_schemas and @docs/intent_operation_reference.md serve as the centralized document for me to review and centralized document for system to check for the respective operation-specific intent-schema so that I know where to update/modify which schema field or make it required or not based on the schema action.



NOTE:
Prompt
  ↓
Intent Classification (Category + Operation) {Not just single classifcation result, but it can contain more than one operation category}
  ↓
Operation-Specific Schema
  ↓
Scoped Intent Tree (Category View)
  ↓
Validation / Disambiguation