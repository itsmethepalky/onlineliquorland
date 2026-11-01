
const fs = require('fs');
const postgres = require('postgres');

const comparison = JSON.parse(
  fs.readFileSync('db-comparison.json', 'utf8')
);

const oldDb = JSON.parse(
  fs.readFileSync('old-db-inventory.json', 'utf8')
);

const quoteIdent = (value) =>
  '"' + String(value).replace(/"/g, '""') + '"';

const quoteRole = (role) =>
  String(role).toLowerCase() === 'public'
    ? 'PUBLIC'
    : quoteIdent(role);

function createPolicySQL(p) {
  const roles = Array.isArray(p.roles) ? p.roles : [p.roles];

  let statement =
    `CREATE POLICY ${quoteIdent(p.policyname)} ` +
    `ON ${quoteIdent(p.schemaname)}.${quoteIdent(p.tablename)} ` +
    `AS ${p.permissive || 'PERMISSIVE'} ` +
    `FOR ${p.cmd} ` +
    `TO ${roles.map(quoteRole).join(', ')}`;

  if (p.qual) {
    statement += `\nUSING (${p.qual})`;
  }

  if (p.with_check) {
    statement += `\nWITH CHECK (${p.with_check})`;
  }

  return statement + ';';
}

async function main() {
  if (!process.env.DATABASE_URL) {
    throw new Error(
      'Old project DATABASE_URL is missing. Set it securely first.'
    );
  }

  const sql = postgres(process.env.DATABASE_URL, {
    ssl: 'require',
    connect_timeout: 30,
    max: 1,
    prepare: false,
  });

  try {
    const missingPolicies = comparison.policies.missing;

    const oldFunctions = oldDb.functions;
    const missingFunctions = comparison.functions.missing;

    const policySQL = missingPolicies.map(createPolicySQL);

    const functionSQL = [];

    for (const fn of missingFunctions) {
      const definitions = await sql`
        SELECT pg_get_functiondef(p.oid) AS definition
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = ${fn.schema_name}
          AND p.proname = ${fn.function_name}
          AND pg_get_function_identity_arguments(p.oid) = ${fn.arguments}
      `;

      if (definitions.length !== 1) {
        throw new Error(
          `Could not uniquely identify function: ${fn.schema_name}.${fn.function_name}(${fn.arguments})`
        );
      }

      functionSQL.push(definitions[0].definition);
    }

    const output = [
      "-- REVIEW ONLY: generated from the old project's inventory.",
      "-- Do not execute until dependencies and RLS status are checked.",
      '',
      "-- Missing RLS policies",
      ...policySQL.flatMap((statement) => [statement, '']),
      "-- Missing function definitions",
      ...functionSQL.flatMap((statement) => [statement, '', '']),
    ].join('\n');

    fs.writeFileSync('review-missing-objects.sql', output, {
      encoding: 'utf8',
      flag: 'wx',
    });

    console.log('Created review-missing-objects.sql');
    console.log('Missing policies:', policySQL.length);
    console.log('Missing functions:', functionSQL.length);
    console.log('No database objects were changed.');
  } finally {
    await sql.end();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
