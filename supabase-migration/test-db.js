const postgres = require('postgres');

const sql = postgres(process.env.DATABASE_URL, {
  ssl: 'require',
  connect_timeout: 10
});

(async () => {
  try {
    const result = await sql`SELECT current_user, current_database()`;
    console.log('Connection successful:', result);
  } catch (error) {
    console.error('Connection failed:', error.message);
  } finally {
    await sql.end();
  }
})();
