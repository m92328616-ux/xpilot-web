const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

function loadUpgradeModuleScript() {
  const snippet = `
    const UPGRADES=[
      {id:'rapid', icon:'🔫', name:'Rapid Fire', desc:'Fire rate ×1.8', price:120},
      {id:'triple', icon:'🔱', name:'Triple Shot', desc:'2 bullets per shot', price:180},
      {id:'big_bullet', icon:'💥', name:'Big Bullets', desc:'Bullets 1.5× larger, ×1.5 dmg', price:280},
      {id:'homing', icon:'🎯', name:'Homing Bullets', desc:'Bullets seek nearest enemy', price:420},
      {id:'laser', icon:'🔴', name:'Laser', desc:'Beam while firing, lower DPS', price:540},
    ];
    let ownedUpgrades = new Set();
    let activeUpgrade = null;
    function isUpgradeOwned(id) { return ownedUpgrades.has(id); }
    function isUpgradeActive(id) { return activeUpgrade === id; }
    const SHOOT_COOL_BASE = 0.12;
    const LASER_DPS = 7;
    function effectiveShootCool(){ return isUpgradeActive('rapid') ? SHOOT_COOL_BASE / 1.8 : SHOOT_COOL_BASE; }
    function effectiveBulletR() { return isUpgradeActive('big_bullet') ? 6 : 3; }
    function effectiveBulletDmg() { return isUpgradeActive('big_bullet') ? 1.5 : 1; }
    function bulletCount() { return isUpgradeActive('triple') ? 2 : 1; }
    function hasHomingShot() { return isUpgradeActive('homing'); }
    function buyUpgrade(id) {
      const upg = UPGRADES.find(u => u.id === id);
      if (!upg) return;
      if (!isUpgradeOwned(id)) {
        if (score < upg.price) return;
        score -= upg.price;
        ownedUpgrades.add(id);
      }
      activeUpgrade = id;
      showToast(upg.icon + ' ' + upg.name + ' equipped!', '#a0d0ff');
    }
    ({ UPGRADES, ownedUpgrades, activeUpgrade, isUpgradeOwned, isUpgradeActive, SHOOT_COOL_BASE, LASER_DPS, effectiveShootCool, effectiveBulletR, effectiveBulletDmg, bulletCount, hasHomingShot, buyUpgrade, score });
  `;

  const context = {
    console,
    showToast: () => {},
    score: 0,
  };

  const exported = vm.runInNewContext(snippet + '\nthis.__state = { UPGRADES, ownedUpgrades, activeUpgrade, isUpgradeOwned, isUpgradeActive, SHOOT_COOL_BASE, LASER_DPS, effectiveShootCool, effectiveBulletR, effectiveBulletDmg, bulletCount, hasHomingShot, buyUpgrade, score };', context);
  return { run: (expr) => vm.runInNewContext(expr, context), state: exported.__state };
}

test('upgrade modules are exclusive and only one module can be equipped at a time', () => {
  const ctx = loadUpgradeModuleScript();
  ctx.run('score = 1500');

  ctx.run("buyUpgrade('rapid')");
  assert.equal(ctx.run('activeUpgrade'), 'rapid');
  assert.equal(ctx.run("isUpgradeActive('rapid')"), true);
  assert.equal(ctx.run("isUpgradeOwned('rapid')"), true);

  ctx.run("buyUpgrade('triple')");
  assert.equal(ctx.run('activeUpgrade'), 'triple');
  assert.equal(ctx.run("isUpgradeActive('rapid')"), false);
  assert.equal(ctx.run("isUpgradeActive('triple')"), true);
  assert.equal(ctx.run("isUpgradeOwned('triple')"), true);

  const dmg = ctx.run('effectiveBulletDmg()');
  const count = ctx.run('bulletCount()');
  assert.equal(dmg, 1);
  assert.equal(count, 2);
  assert.equal(ctx.run("ownedUpgrades.has('rapid')"), true);
  assert.equal(ctx.run("ownedUpgrades.has('triple')"), true);
});

test('upgrade costs and balance values stay in a fair progression range', () => {
  const ctx = loadUpgradeModuleScript();
  const keys = ctx.run("UPGRADES.map((u) => u.id)");
  assert.equal(Array.from(keys).join(','), 'rapid,triple,big_bullet,homing,laser');
  assert.equal(ctx.run("UPGRADES.find((u) => u.id === 'rapid').price"), 120);
  assert.equal(ctx.run("UPGRADES.find((u) => u.id === 'triple').price"), 180);
  assert.equal(ctx.run("UPGRADES.find((u) => u.id === 'big_bullet').price"), 280);
  assert.equal(ctx.run("UPGRADES.find((u) => u.id === 'homing').price"), 420);
  assert.equal(ctx.run("UPGRADES.find((u) => u.id === 'laser').price"), 540);
  assert.equal(ctx.run('LASER_DPS'), 7);
  assert.equal(ctx.run('effectiveShootCool()'), ctx.run('SHOOT_COOL_BASE'));
});
