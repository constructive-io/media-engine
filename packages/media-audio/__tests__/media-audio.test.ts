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

describe('media_audio.emotions', () => {
  it('ships a delivery vocabulary with a neutral fallback', async () => {
    const { rows } = await pg.query(`SELECT slug FROM media_audio.emotions ORDER BY sort_order`);
    expect(rows[0].slug).toBe('neutral');
    expect(rows.length).toBeGreaterThan(1);
  });
});

describe('media_audio.text_models', () => {
  it('belongs to a persona from the shared spine', async () => {
    await expect(
      pg.query(
        `INSERT INTO media_audio.text_models (persona_id, model_ref)
         VALUES ('00000000-0000-0000-0000-000000000000', 'example-model')`
      )
    ).rejects.toThrow(/foreign key|violates/);
  });
});
