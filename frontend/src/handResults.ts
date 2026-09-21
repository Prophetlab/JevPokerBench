import type {State} from './types';
// Payoff is net profit. A side-pot recipient can still lose money overall.
export function handWinners(state:State){
    return state.complete?state.seats.filter(seat=>!seat.folded&&seat.payoff!==null&&seat.committed+seat.payoff>0):[];
}
