import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';
const en = JSON.parse(fs.readFileSync(new URL('../src/locales/en.json', import.meta.url), 'utf8'));
const zh = JSON.parse(fs.readFileSync(new URL('../src/locales/zh.json', import.meta.url), 'utf8'));
const source = fs.readFileSync(new URL('../src/translations.ts', import.meta.url), 'utf8')
  .replace("import en from './locales/en.json';", `const en = ${JSON.stringify(en)};`)
  .replace("import zh from './locales/zh.json';", `const zh = ${JSON.stringify(zh)};`);
const output = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText;
const {translate} = await import('data:text/javascript;base64,' + Buffer.from(output).toString('base64'));
test('English catalog preserves every interpolation placeholder', () => {
  const slots = text => (text.match(/\{\d+\}/g) || []).sort();
  for (const [key, value] of Object.entries(en)) assert.deepEqual(slots(value), slots(key), key);
});
test('Every literal UI translation has an English entry', () => {
  for (const name of fs.readdirSync(new URL('../src/', import.meta.url)).filter(name => name.endsWith('.tsx'))) {
    const source = ts.createSourceFile(name, fs.readFileSync(new URL('../src/' + name, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const visit = node => {
      if (ts.isCallExpression(node) && node.expression.getText(source) === 't' && ts.isStringLiteral(node.arguments[0])) {
        const key = node.arguments[0].text;
        if (/\p{Script=Han}/u.test(key)) assert.ok(key in en, `${name}: ${key}`);
      }
      if (ts.isJsxText(node) && node.text !== '中文') assert.ok(!/\p{Script=Han}/u.test(node.text), `${name}: unlocalized JSX text: ${node.text}`);
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
});
test('Nested server validation errors and retry failures retain their details', () => {
  assert.equal(translate('第 2 条事件（abc）：当前应发 3 张公共牌', 'en'), 'Event 2 (abc): Deal 3 board cards at this point');
  assert.equal(translate('模型概率之和不为 1；已尝试 3 次，未执行动作', 'en'), 'Model probabilities do not sum to 1; 3 attempts made, no action executed');
  assert.equal(translate('cash_small_blind：小盲不能大于大盲', 'en'), 'cash_small_blind: Small blind cannot exceed the big blind');
});
test('Saved event labels, equity assumptions and hand counts localize at render time', () => {
  assert.equal(translate('庄位轮转完成 · 第 2 轮随机换座', 'en'), 'Button orbit complete · Seats shuffled for orbit 2');
  assert.equal(translate('公共牌 Ah 7c 2d', 'en'), 'Board: Ah 7c 2d');
  assert.equal(translate('第 {0} 手', 'en', 2), 'Hand 2');
  assert.equal(translate('第 {0} 手', 'zh', 2), '第 2 手');
  assert.equal(translate('玩家 6', 'en'), 'Player 6');
  assert.equal(translate('CALL · ALL IN', 'zh'), '跟注 · 全下');
  assert.ok(!/\p{Script=Han}/u.test(translate('未录入实际后手，统一假设开手 100BB；短码/全下/边池建议须改用实际后手', 'en')));
});
test('Unknown text is preserved and interpolated data is not interpreted as markup', () => {
  assert.equal(translate('My custom match', 'en'), 'My custom match');
  assert.equal(translate('第 {0} 名', 'en', '<script>'), 'Place <script>');
});
