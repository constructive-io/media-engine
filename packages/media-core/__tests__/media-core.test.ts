import { getConnections, PgTestClient } from 'pgsql-test';

let pg: PgTestClient;
let teardown: () => Promise<void>;

beforeAll(async () => {
  ({ pg, teardown } = await getConnections());
});

afterAll(async () => {
  await teardown();
});

beforeEach(async () => {
  await pg.beforeEach();
});

afterEach(async () => {
  await pg.afterEach();
});

const persona = async (slug: string): Promise<string> => {
  const { rows } = await pg.query(
    `INSERT INTO media.personas (slug, name) VALUES ($1, $2) RETURNING id`,
    [slug, 'Ada Example']
  );
  return rows[0].id;
};

describe('media.personas', () => {
  it('defaults to a real person and keeps slugs unique', async () => {
    await persona('ada');
    const { rows } = await pg.query(`SELECT kind FROM media.personas WHERE slug = 'ada'`);
    expect(rows[0].kind).toBe('person');
    await expect(persona('ada')).rejects.toThrow(/duplicate key/);
  });
});

describe('media.voices', () => {
  it('requires an engine and a profile for a synthesis voice', async () => {
    const id = await persona('ada');
    await expect(
      pg.query(`INSERT INTO media.voices (persona_id, direction) VALUES ($1, 'synthesis')`, [id])
    ).rejects.toThrow(/check constraint/);
  });

  it('holds both directions for one persona', async () => {
    const id = await persona('ada');
    await pg.query(
      `INSERT INTO media.voices (persona_id, direction, engine, profile_ref)
       VALUES ($1, 'synthesis', 'example-engine', 'profiles/ada')`,
      [id]
    );
    await pg.query(
      `INSERT INTO media.voices (persona_id, direction, median_f0) VALUES ($1, 'acoustic', 180)`,
      [id]
    );
    const { rows } = await pg.query(
      `SELECT direction FROM media.voices WHERE persona_id = $1 ORDER BY direction`,
      [id]
    );
    expect(rows.map((r: { direction: string }) => r.direction)).toEqual(['acoustic', 'synthesis']);
  });
});

describe('media.assets', () => {
  it('ingests a file once, whatever the medium', async () => {
    const insert = (medium: string) =>
      pg.query(
        `INSERT INTO media.assets (medium, path, content_hash) VALUES ($1, 'clips/example.mov', 'hash-1')`,
        [medium]
      );
    await insert('video');
    await expect(insert('audio')).rejects.toThrow(/duplicate key/);
  });
});
