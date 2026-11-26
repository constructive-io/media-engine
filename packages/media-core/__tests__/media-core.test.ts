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

describe('media.takes', () => {
  const asset = async (name: string): Promise<string> => {
    const { rows } = await pg.query(
      `INSERT INTO media.assets (medium, path, content_hash) VALUES ('audio', $1, $1) RETURNING id`,
      [`clips/${name}.wav`]
    );
    return rows[0].id;
  };

  it('records how each offset was arrived at, and only in known ways', async () => {
    const { rows } = await pg.query(`INSERT INTO media.takes (name) VALUES ('example-take') RETURNING id`);
    const take = rows[0].id;
    const add = async (name: string, method: string | null) =>
      pg.query(
        `INSERT INTO media.take_assets (take_id, asset_id, role, offset_s, offset_method)
         VALUES ($1, $2, 'lav', 1.5, $3)`,
        [take, await asset(name), method]
      );
    await add('unplaced', null);
    for (const method of ['words', 'phrases', 'loudness', 'clock', 'manual']) {
      await add(method, method);
    }
    await pg.query('SAVEPOINT before_guess');
    await expect(add('guessed', 'guess')).rejects.toThrow(/check constraint/);
    await pg.query('ROLLBACK TO SAVEPOINT before_guess');
    const placed = await pg.query(
      `SELECT count(*)::int AS n FROM media.take_assets WHERE take_id = $1 AND offset_method IS NOT NULL`,
      [take]
    );
    expect(placed.rows[0].n).toBe(5);
  });

  it('has one primary recording at most', async () => {
    const { rows } = await pg.query(`INSERT INTO media.takes (name) VALUES ('example-take') RETURNING id`);
    const primary = async (name: string) =>
      pg.query(
        `INSERT INTO media.take_assets (take_id, asset_id, role, is_primary) VALUES ($1, $2, 'camera', true)`,
        [rows[0].id, await asset(name)]
      );
    await primary('first');
    await expect(primary('second')).rejects.toThrow(/duplicate key/);
  });
});
