/** Release tags may originate from dev or a published iteration branch (RELYYYYMMDD). */
import { execFileSync, spawnSync } from 'node:child_process';
const refs = execFileSync('git', ['for-each-ref', '--format=%(refname)', 'refs/remotes/origin'], { encoding: 'utf8' })
  .trim().split('\n').filter(ref => /^refs\/remotes\/origin\/(?:dev|REL[0-9]{8})$/.test(ref));
const matches = refs.filter(ref => {
  const result = spawnSync('git', ['merge-base', '--is-ancestor', 'HEAD', ref], { encoding: 'utf8' });
  if (result.error) throw result.error;
  if (result.status !== 0 && result.status !== 1) throw new Error(`Unable to validate release ancestry for ${ref}`);
  return result.status === 0;
});
if (!matches.length) throw new Error('Release commit must be reachable from remote origin/dev or an origin/RELYYYYMMDD iteration branch (exactly eight digits, no suffix). Push the reviewed release commit to the chosen branch before tagging.');
console.log(`PASS: release commit is on ${matches.map(ref => ref.replace('refs/remotes/', '')).join(', ')}`);
