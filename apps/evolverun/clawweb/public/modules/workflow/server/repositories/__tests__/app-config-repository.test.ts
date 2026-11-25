import Database from 'better-sqlite3';
import { afterEach, describe, expect, it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { AppConfigRepository } from '../app-config-repository.js';
import { createAIStudioStateFixture } from './aistudio-train-state.fixture.js';
const connections:Database.Database[]=[];
afterEach(() => {for(const raw of connections.splice(0)) raw.close();});
function fixture() {
  const raw=new Database(':memory:'); connections.push(raw); createAIStudioStateFixture(raw);
  const repo=new AppConfigRepository(new SqliteDatabase(raw));
  for(const key of ['aisw:state','aisa:audit','AISW:ci-alias','AISA:ci-alias']) raw.prepare('INSERT INTO cm_app_config(config_key,config_yaml,enabled) VALUES(?,?,?)').run(key,'{}',1);
  return {raw,repo};
}
describe('generic config reserved AIStudio namespace isolation', () => {
  it('excludes reserved rows even if enabled and requested via an empty/broad prefix', async () => {
    const {repo}=fixture(); await repo.create({config_key:'normal',config_yaml:'value: ok'});
    expect((await repo.listAll()).map(r => r.config_key)).toEqual(['normal']);
    expect((await repo.listAll(true)).map(r => r.config_key)).toEqual(['normal']);
    expect((await repo.listByPrefix('')).map(r => r.config_key)).toEqual(['normal']);
    expect(await repo.listByPrefix('ais')).toEqual([]);
    for(const key of ['aisw:state','aisa:audit','AISW:ci-alias','AISA:ci-alias']) expect(await repo.findByKey(key)).toBeNull();
  });
  it.each(['aisw:new','aisa:new','AISW:new','AISA:new'])('rejects every generic writer before touching reserved key %s', async key => {
    const {repo,raw}=fixture(); const before=raw.prepare('SELECT * FROM cm_app_config').all();
    await expect(repo.create({config_key:key,config_yaml:'{}'})).rejects.toThrow(/reserved/i);
    await expect(repo.update(key,{config_yaml:'{}'})).rejects.toThrow(/reserved/i);
    await expect(repo.delete(key)).rejects.toThrow(/reserved/i);
    for(const expectedVersion of [0,1]) await expect(repo.compareAndSet(key,{configYaml:'{}',enabled:true,updatedBy:'user',expectedVersion})).rejects.toThrow(/reserved/i);
    expect(raw.prepare('SELECT * FROM cm_app_config').all()).toEqual(before);
  });
  it('preserves generic CRUD, literal prefix escaping and version CAS for normal config', async () => {
    const {repo}=fixture(); await repo.create({config_key:'normal_%',config_yaml:'a: 1'});
    expect(await repo.listByPrefix('normal_%')).toHaveLength(1);
    expect((await repo.update('normal_%',{enabled:0}))!.version).toBe(2);
    const input={configYaml:'a: 2',enabled:true,updatedBy:'user',expectedVersion:2};
    expect((await repo.compareAndSet('normal_%',input)).version).toBe(3);
    await expect(repo.compareAndSet('normal_%',input)).rejects.toThrow('SETTINGS_CONFLICT');
    expect(await repo.delete('normal_%')).toBe(true); expect(await repo.findByKey('normal_%')).toBeNull();
  });
});
