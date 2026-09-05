import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
const root=process.cwd();
const targets=['src','assets','README.md','package.json','package-lock.json','vite.config.ts','tsconfig.json','dist/index.umd.js'];
const deniedPatterns=[/antgroup/i,/tnpm/i,/yuyan/i,/code\.alipay/i,/registry\.antgroup/i,/Bearer\b/,/Authorization\b/,/document\.cookie/];
const runtimeImageUrl=/(?:src=|url\(|href=)[^\n]{0,120}https?:\/\/(?!kenney\.nl)/i;
const ignoredDirs=new Set(['node_modules']);
function collectFiles(path){const stat=statSync(path);if(stat.isFile())return[path];if(!stat.isDirectory())return[];return readdirSync(path).flatMap(entry=>ignoredDirs.has(entry)?[]:collectFiles(join(path,entry)))}
const findings=[];
for(const target of targets){const targetPath=join(root,target);if(!existsSync(targetPath))continue;for(const file of collectFiles(targetPath)){const rel=relative(root,file);if(/\.(png|jpg|jpeg|gif)$/i.test(file))continue;const text=readFileSync(file,'utf8');const normalized=rel==='package.json'?JSON.stringify({...JSON.parse(text),scripts:undefined,repository:undefined},null,2):text;for(const pattern of deniedPatterns)if(pattern.test(normalized))findings.push(`${rel}: forbidden pattern ${pattern}`);if((rel.startsWith('src/')||rel==='dist/index.umd.js')&&runtimeImageUrl.test(text))findings.push(`${rel}: third-party runtime image URL`)}}
for(const required of ['assets/THIRD_PARTY_NOTICES.md','assets/SELECTION.md','assets/ASSET_GUIDE.md','assets/source/kenney-rpg-urban/License.txt','assets/source/kenney-roguelike-rpg/License.txt'])if(!existsSync(join(root,required)))findings.push(`${required}: required provenance file is missing`);
const atlases=['src/assets/room-atlas.svg','src/assets/character-atlas.svg','src/assets/ui-atlas.svg','src/assets/speech-frame.svg','src/assets/speech-tail.svg','src/assets/knight-statue.svg'];const atlasBytes=atlases.reduce((sum,file)=>sum+(existsSync(join(root,file))?statSync(join(root,file)).size:0),0);if(atlasBytes>120*1024)findings.push(`production atlases: ${atlasBytes} bytes exceeds 122880-byte budget`);
const dist=join(root,'dist/index.umd.js');const distBytes=existsSync(dist)?statSync(dist).size:0;if(distBytes>250*1024)findings.push(`dist/index.umd.js: ${distBytes} bytes exceeds 256000-byte budget`);
const zipFiles=collectFiles(join(root,'assets')).filter(file=>/\.(zip|tar|tgz)$/i.test(file));for(const file of zipFiles)findings.push(`${relative(root,file)}: full source archive must not be committed`);
if(findings.length){console.error('Public/asset scan failed:');findings.forEach(f=>console.error(`- ${f}`));process.exit(1)}
console.log(`Public/asset scan passed: atlases ${atlasBytes} / 122880 bytes; UMD ${distBytes} / 256000 bytes; provenance complete; no runtime third-party image URLs.`);
