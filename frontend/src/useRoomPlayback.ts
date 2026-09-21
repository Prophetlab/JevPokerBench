import {useEffect, useState} from 'react';
import {GameEvent} from './types';

// Keep every received event available while revealing one frame per second.
// The first load restores the current state; later hands play from the deal.
export function useRoomPlayback(room: string, events: GameEvent[]) {
    const [fast, setFast] = useState(false);
    const [cursor, setCursor] = useState<{room: string; hand: number; index: number} | null>(null);
    const hand = events[0]?.snapshot.hand_number;
    const last = events.length - 1;
    const matches = cursor?.room === room && cursor?.hand === hand;
    const index = fast ? last : matches ? Math.min(cursor!.index, last) : cursor?.room === room ? 0 : last;
    const behind = index < last;
    useEffect(() => {
        if (hand !== undefined && !matches) setCursor({room, hand, index});
    }, [room, hand, matches, index]);
    useEffect(() => {
        if (fast || !matches || !behind) return;
        const timer = setTimeout(() => setCursor(previous => previous?.room === room && previous.hand === hand
            ? {...previous, index: previous.index + 1} : previous), 1000);
        return () => clearTimeout(timer);
    }, [room, hand, matches, behind, cursor?.index, fast]);
    const toggle = () => {
        if (hand !== undefined) setCursor({room, hand, index: last});
        setFast(value => !value);
    };
    return {fast, toggle, behind, events: events.slice(0, index + 1)};
}
