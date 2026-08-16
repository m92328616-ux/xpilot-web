(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.FuelSystem = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function createFuelState(maxFuel = 100, startFuel = 100, regenRate = 2, idleDelay = 3) {
    return {
      maxFuel: Math.max(1, Number(maxFuel) || 100),
      fuel: Math.max(0, Math.min(Number(startFuel) || 0, maxFuel)),
      regenRate: Math.max(0, Number(regenRate) || 0),
      idleDelay: Math.max(0, Number(idleDelay) || 0),
      idleTimer: 0,
    };
  }

  function consumeFuel(state, cost, { allowBelowZero = false } = {}) {
    const fuelCost = Math.max(0, Number(cost) || 0);
    if (!state) return { ok: false, remaining: 0, consumed: 0 };
    const nextFuel = state.fuel - fuelCost;
    if (!allowBelowZero && nextFuel < 0) {
      return { ok: false, remaining: state.fuel, consumed: 0 };
    }
    state.fuel = Math.max(0, Math.min(state.maxFuel, nextFuel));
    return { ok: true, remaining: state.fuel, consumed: fuelCost };
  }

  function tickFuelRegen(state, dt, isActing) {
    if (!state) return state;
    if (isActing) {
      state.idleTimer = 0;
      return state;
    }
    if (state.fuel >= state.maxFuel) {
      state.idleTimer = 0;
      return state;
    }
    state.idleTimer += dt;
    if (state.idleTimer >= state.idleDelay) {
      state.fuel = Math.min(state.maxFuel, state.fuel + state.regenRate * dt);
    }
    return state;
  }

  function getFuelPercent(state) {
    if (!state || !state.maxFuel) return 0;
    return Math.max(0, Math.min(1, state.fuel / state.maxFuel));
  }

  return {
    createFuelState,
    consumeFuel,
    tickFuelRegen,
    getFuelPercent,
  };
});
