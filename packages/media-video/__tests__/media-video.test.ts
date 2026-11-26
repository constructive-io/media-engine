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

const asset = async (): Promise<string> => {
  const { rows } = await pg.query(
    `INSERT INTO media.assets (medium, path, content_hash)
     VALUES ('video', 'clips/example.mov', 'hash-1') RETURNING id`
  );
  return rows[0].id;
};

describe('media_video.shots', () => {
  it('derives a shot duration from its bounds', async () => {
    const id = await asset();
    const { rows } = await pg.query(
      `INSERT INTO media_video.shots (asset_id, idx, start_s, end_s)
       VALUES ($1, 0, 1.5, 4) RETURNING duration_s`,
      [id]
    );
    expect(rows[0].duration_s).toBe(2.5);
  });

  it('goes away with the asset it was cut from', async () => {
    const id = await asset();
    await pg.query(
      `INSERT INTO media_video.shots (asset_id, idx, start_s, end_s) VALUES ($1, 0, 0, 1)`,
      [id]
    );
    await pg.query(`DELETE FROM media.assets WHERE id = $1`, [id]);
    const { rows } = await pg.query(`SELECT count(*)::int AS n FROM media_video.shots`);
    expect(rows[0].n).toBe(0);
  });
});

describe('media_video.decisions', () => {
  it('records who decided without being told', async () => {
    const { rows } = await pg.query(
      `INSERT INTO media_video.decisions (kind, keep) VALUES ('shot_keep', true)
       RETURNING actor, source_of, actor = current_user AS mine`
    );
    expect(rows[0].mine).toBe(true);
    expect(rows[0].source_of).toBe('human');
  });
});

describe('media_video.vocabulary', () => {
  it('ships empty: the words belong to the project, not the engine', async () => {
    const { rows } = await pg.query(`SELECT count(*)::int AS n FROM media_video.vocabulary`);
    expect(rows[0].n).toBe(0);
  });
});
