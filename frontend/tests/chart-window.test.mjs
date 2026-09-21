import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';
const source=fs.readFileSync(new URL('../src/chartWindow.ts',import.meta.url),'utf8');
const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {clampRange,zoomRange}=await import('data:text/javascript;base64,'+Buffer.from(output).toString('base64'));
test('Empty and one-point histories have no invalid slider bounds',()=>{
    for(const count of [0,1]){
        assert.deepEqual(clampRange([100,200],count),[0,0]);
        assert.deepEqual(zoomRange([0,0],count,.5),[0,0]);
    }
});
test('Zoom preserves at least two points and expands fully at either edge',()=>{
    let range=[0,5000];
    for(let i=0;i<20;i++)range=zoomRange(range,5001,.5);
    assert.equal(range[1]-range[0],1);
    assert.deepEqual(zoomRange([0,50],101,2),[0,100]);
    assert.deepEqual(zoomRange([50,100],101,2),[0,100]);
});
test('Live updates preserve the selected interval and shorter data clamps safely',()=>{
    assert.deepEqual(clampRange([20,40],200),[20,40]);
    assert.deepEqual(clampRange([20,40],10),[8,9]);
});
