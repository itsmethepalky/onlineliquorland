
const fs = require('fs');

const oldDb = JSON.parse(
  fs.readFileSync('old-db-inventory.json', 'utf8')
);

const newDb = JSON.parse(
  fs.readFileSync('new-db-inventory.json', 'utf8')
);

function key(row, fields) {
  return fields.map(field => String(row[field] ?? '')).join('|');
}

function compare(label, oldRows, newRows, fields) {
  const oldMap = new Map(oldRows.map(row => [key(row, fields), row]));
  const newMap = new Map(newRows.map(row => [key(row, fields), row]));

  const missing = [...oldMap.entries()]
    .filter(([k]) => !newMap.has(k))
    .map(([, row]) => row);

  const added = [...newMap.entries()]
    .filter(([k]) => !oldMap.has(k))
    .map(([, row]) => row);

  console.log(`\n${label}`);
  console.log('Missing from new:', missing.length);
  console.log('Only in new:', added.length);

  return { missing, added };
}

const report = {
  tables: compare(
    'TABLES',
    oldDb.tables,
    newDb.tables,
    ['schemaname', 'tablename']
  ),
  policies: compare(
    'POLICIES',
    oldDb.policies,
    newDb.policies,
    ['schemaname', 'tablename', 'policyname']
  ),
  functions: compare(
    'FUNCTIONS',
    oldDb.functions,
    newDb.functions,
    ['schema_name', 'function_name', 'arguments']
  ),
  triggers: compare(
    'TRIGGERS',
    oldDb.triggers,
    newDb.triggers,
    [
      'schema_name',
      'table_name',
      'trigger_name',
      'action_timing',
      'event_manipulation'
    ]
  )
};

fs.writeFileSync(
  'db-comparison.json',
  JSON.stringify(report, null, 2)
);

console.log('\nComparison saved to db-comparison.json');
console.log('This script did not modify either database.');
