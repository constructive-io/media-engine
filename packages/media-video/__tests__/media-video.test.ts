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

describe('media_video.words', () => {
  it('keeps what was heard beside a word that is mended, and nothing beside one that is not', async () => {
    const id = await asset();
    const { rows: segments } = await pg.query(
      `INSERT INTO media_video.segments (asset_id, idx, start_s, end_s, text)
       VALUES ($1, 0, 0, 1, 'the tied') RETURNING id`,
      [id]
    );
    const { rows } = await pg.query(
      `INSERT INTO media_video.words (segment_id, idx, word)
       VALUES ($1, 0, 'the'), ($1, 1, 'tied') RETURNING word_asr`,
      [segments[0].id]
    );
    expect(rows.map((row) => row.word_asr)).toEqual([null, null]);
    const mended = await pg.query(
      `UPDATE media_video.words SET word_asr = coalesce(word_asr, word), word = 'tide'
        WHERE segment_id = $1 AND idx = 1 RETURNING word, word_asr`,
      [segments[0].id]
    );
    expect(mended.rows[0]).toEqual({ word: 'tide', word_asr: 'tied' });
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

describe('media_video.speaker_clusters', () => {
  it('keeps what a voice sounds like, and how it was found', async () => {
    const assetId = await asset();
    const { rows } = await pg.query(
      `INSERT INTO media_video.speaker_clusters
         (asset_id, label, method, model, centroid, cohesion, n_windows, speech_s)
       VALUES ($1, 'voice1', 'voice-embedding', 'example-model', '{0.6,0.8}', 0.74, 40, 61.5)
       RETURNING centroid, cohesion, n_windows, speech_s`,
      [assetId]
    );
    expect(rows[0]).toEqual({ centroid: [0.6, 0.8], cohesion: 0.74, n_windows: 40, speech_s: 61.5 });
  });

  it('asks how a voice was found instead of assuming', async () => {
    const assetId = await asset();
    await expect(
      pg.query(`INSERT INTO media_video.speaker_clusters (asset_id, label) VALUES ($1, 'voice1')`, [assetId])
    ).rejects.toThrow(/method/);
  });

  it('gives a segment the name its voice was given', async () => {
    const assetId = await asset();
    const persona = await pg.query(
      `INSERT INTO media.personas (slug, name) VALUES ('ada-example', 'Ada Example') RETURNING id`
    );
    const voice = await pg.query(
      `INSERT INTO media_video.speaker_clusters (asset_id, label, method)
       VALUES ($1, 'voice1', 'voice-embedding') RETURNING id`,
      [assetId]
    );
    await pg.query(
      `INSERT INTO media_video.segments (asset_id, idx, start_s, end_s, text, cluster_id)
       VALUES ($1, 0, 0, 2, 'made-up words', $2)`,
      [assetId, voice.rows[0].id]
    );
    const who = () => pg.query(`SELECT who FROM media_video.v_dialogue WHERE asset_id = $1`, [assetId]);
    expect((await who()).rows[0].who).toBe('voice1');
    await pg.query(`UPDATE media_video.speaker_clusters SET persona_id = $1`, [persona.rows[0].id]);
    expect((await who()).rows[0].who).toBe('Ada Example');
  });
});
