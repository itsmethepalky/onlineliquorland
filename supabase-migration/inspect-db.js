
const postgres = require('postgres');
const fs = require('fs');

const sql = postgres(process.env.DATABASE_URL, {
  ssl: 'require',
  connect_timeout: 30,
  idle_timeout: 10,
  max: 1,
  prepare: false,
});

async function main() {
  const inventory = {};

  try {
    console.log('Checking database connection...');
    inventory.connection = await sql`
      SELECT current_database() AS database,
             current_user AS username
    `;

    console.log('Checking tables...');
    inventory.tables = await sql`
      SELECT schemaname, tablename, rowsecurity
      FROM pg_tables
      JOIN pg_namespace ON pg_namespace.nspname = schemaname
      WHERE schemaname IN ('public', 'storage')
      ORDER BY schemaname, tablename
    `;

    console.log('Checking RLS policies...');
    inventory.policies = await sql`
      SELECT schemaname, tablename, policyname, roles, cmd,
             qual, with_check
      FROM pg_policies
      WHERE schemaname IN ('public', 'storage')
      ORDER BY schemaname, tablename, policyname
    `;

    console.log('Checking functions...');
    inventory.functions = await sql`
      SELECT n.nspname AS schema_name,
             p.proname AS function_name,
             pg_get_function_identity_arguments(p.oid) AS arguments
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public'
      ORDER BY p.proname
    `;

    console.log('Checking triggers...');
    inventory.triggers = await sql`
      SELECT event_object_schema AS schema_name,
             event_object_table AS table_name,
             trigger_name,
             action_timing,
             event_manipulation
      FROM information_schema.triggers
      WHERE event_object_schema IN ('public', 'storage')
      ORDER BY event_object_schema, event_object_table, trigger_name
    `;

    fs.writeFileSync(
      'old-db-inventory.json',
      JSON.stringify(inventory, null, 2)
    );

    console.log('Inventory saved successfully.');
    console.log('Tables:', inventory.tables.length);
    console.log('Policies:', inventory.policies.length);
    console.log('Functions:', inventory.functions.length);
    console.log('Triggers:', inventory.triggers.length);
  } catch (error) {
    console.error('Inspection failed:', error.message);
  } finally {
    await sql.end();
  }
}

main();
