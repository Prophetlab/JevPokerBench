import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';
const source=fs.readFileSync(new URL('../src/types.ts',import.meta.url),'utf8');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText.replace(/export \{ api \} from '\.\/api';?/, '');
const {seriesPoints}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const ten={standings:Array(10)};
test('Ten-seat completed events award 9 through 0 points',()=>{
  assert.deepEqual(Array.from({length:10},(_,i)=>seriesPoints(ten,{completed:1,mean_place:i+1})),[9,8,7,6,5,4,3,2,1,0]);
  assert.equal(seriesPoints(ten,{completed:0,mean_place:null}),null);
});
test('Series points sum placements exactly across fractional means',()=>{
  // Finishes 1, 5, 10 award 9 + 5 + 0; the fractional mean must not round the total early.
  assert.equal(seriesPoints(ten,{completed:3,mean_place:16/3}),14);
  assert.equal(seriesPoints(ten,{completed:3,mean_place:5}),15);
  assert.equal(seriesPoints(ten,{completed:50,mean_place:4.2}),290);
});
test('Smaller series retain N-minus-place scoring',()=>{
  const headsUp={standings:Array(2)};
  assert.equal(seriesPoints(headsUp,{completed:2,mean_place:1.5}),1);
  assert.equal(seriesPoints(headsUp,{completed:2,mean_place:2}),0);
});
