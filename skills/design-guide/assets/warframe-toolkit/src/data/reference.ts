import { catalog } from './catalog';
import { createEquipment } from '../lib/model';
import type { Equipment, Inventory, Slot } from '../lib/model';
import type { Relic } from '../lib/relics';

// Manual transcription of the 35 visible rows in the user's reference image,
// plus sample Warframe and Relic entries so all collection tabs are populated.
type ReferenceRow = [name: string, owned: boolean, dontKeep: boolean, target: string, incarnon: boolean, quest: boolean, upgraded: boolean, powerful: boolean, prime: boolean, slot: Slot];
const rows: ReferenceRow[] = [
  ['Akzani', false, true, '', false, false, false, false, false, 'Secondary'],
  ['Bo', true, true, 'Tipedo; Cadus', true, false, false, false, true, 'Melee'],
  ['Boltor', true, true, 'Boltace', true, false, true, false, true, 'Primary'],
  ['Galatine', true, true, 'Paracesis', false, false, false, false, true, 'Melee'],
  ['Hikou', true, true, 'Panthera', false, false, false, false, true, 'Secondary'],
  ['Karyst', true, true, '', false, false, false, false, true, 'Melee'],
  ['Knell', true, true, '', false, false, false, false, true, 'Secondary'],
  ['Lex', false, true, 'Aklex', true, false, false, true, true, 'Secondary'],
  ['MK1-Paris', false, true, '', true, false, false, false, true, 'Primary'],
  ['MK1-Strun', false, true, '', true, false, false, false, true, 'Primary'],
  ['Mutalist Cernos', true, true, 'Proboscis Cernos', false, false, false, false, false, 'Primary'],
  ['Skana', false, true, '', true, false, false, false, true, 'Melee'],
  ['Anku', true, false, '', true, false, false, true, false, 'Melee'],
  ['Arca Titron', true, false, '', false, false, false, true, false, 'Melee'],
  ['Atomos', true, false, 'Twin Basolk', true, false, false, true, false, 'Secondary'],
  ['Braton Prime', true, false, '', true, false, false, true, false, 'Primary'],
  ['Broken Scepter', true, false, '', false, true, true, false, false, 'Melee'],
  ['Broken War', true, false, 'War', false, true, true, true, false, 'Melee'],
  ['Bronco', true, false, 'Akbronco', true, false, false, true, true, 'Secondary'],
  ['Despair', true, false, '', true, false, false, true, false, 'Secondary'],
  ['Dread', true, false, '', true, false, false, true, false, 'Primary'],
  ['Fang Prime', true, false, '', false, false, false, true, false, 'Melee'],
  ['Furax', true, false, 'Knux', true, false, false, true, false, 'Melee'],
  ['Furis', true, false, 'Afuris', true, false, false, true, false, 'Secondary'],
  ['Gammacor', true, false, 'Heliocor', true, false, false, false, false, 'Secondary'],
  ['Grimoire', true, false, '', false, true, true, true, false, 'Secondary'],
  ['Hirudo', true, false, '', false, false, false, true, false, 'Melee'],
  ['Ignis Wraith', true, false, '', false, false, false, false, false, 'Primary'],
  ['Kunai', true, false, 'Tipedo', true, false, false, true, false, 'Secondary'],
  ['Lato Vandal', true, false, '', true, false, false, true, false, 'Secondary'],
  ['Lex Prime', true, false, 'Aklex Prime', true, false, false, true, false, 'Secondary'],
  ['Magistar', true, false, 'Sibear', true, false, false, true, false, 'Melee'],
  ['MK1-Furax', true, false, '', false, false, false, false, false, 'Melee'],
  ['Nataruk', true, false, '', false, true, true, true, false, 'Primary'],
  ['Orthos Prime', true, false, '', false, false, false, true, false, 'Melee'],
  ['Excalibur', true, false, '', false, false, false, false, false, 'Warframe'],
  ['Excalibur Umbra', true, false, '', false, true, true, true, false, 'Warframe'],
  ['Rhino', false, false, '', false, false, false, false, true, 'Warframe'],
  ['Rhino Prime', true, false, '', false, false, true, true, false, 'Warframe'],
  ['Wisp', true, false, '', false, false, false, false, true, 'Warframe'],
  ['Wisp Prime', true, false, '', false, false, true, true, false, 'Warframe'],
  ['Volt Prime', true, false, '', false, false, true, true, false, 'Warframe'],
  ['Voidrig', true, false, '', false, true, true, true, false, 'Warframe'],
];

const sampleRelics: Relic[] = [
  { id: 'relic-lith-a1-intact', name: 'Lith A1', refinement: 'Intact', quantity: 4, notes: 'Vaulted; keep for radshare', updatedAt: '2026-10-16T00:00:00.000Z' },
  { id: 'relic-lith-n1-radiant', name: 'Lith N1', refinement: 'Radiant', quantity: 2, notes: 'Nova Prime Systems target', updatedAt: '2026-10-16T00:00:00.000Z' },
  { id: 'relic-meso-f2-intact', name: 'Meso F2', refinement: 'Intact', quantity: 3, notes: '', updatedAt: '2026-10-16T00:00:00.000Z' },
  { id: 'relic-neo-v8-radiant', name: 'Neo V8', refinement: 'Radiant', quantity: 1, notes: '', updatedAt: '2026-10-16T00:00:00.000Z' },
  { id: 'relic-axi-h4-intact', name: 'Axi H4', refinement: 'Intact', quantity: 5, notes: '', updatedAt: '2026-10-16T00:00:00.000Z' },
  { id: 'relic-requiem-i-intact', name: 'Requiem I', refinement: 'Intact', quantity: 2, notes: 'Kuva Lich murmurs', updatedAt: '2026-10-16T00:00:00.000Z' },
];

export function initialInventory(): Inventory {
  const items = rows.map(([name, owned, dontKeep, target, incarnon, quest, upgraded, powerful, prime, slot]): Equipment => {
    const info = catalog.find(item => item.name === name);
    const item = createEquipment(info || { name, slot, variant: 'Base' });
    return {
      ...item, owned, dontKeep, craftingTarget: target, craftingIngredient: Boolean(target),
      incarnon, questWeapon: quest, upgraded: upgraded ? 'Yes' : 'Unknown', powerful,
      hasPrimeVariant: prime, mastered: name !== 'Rhino', rank: name === 'Voidrig' ? 40 : name === 'Rhino' ? 18 : 30,
      helminth: name === 'Wisp' ? 'Yes' : name === 'Excalibur' ? 'No' : 'Unknown',
      source: 'Reference spreadsheet · visible rows',
    };
  });
  return { version: 1, items, relics: sampleRelics, imports: [], referenceNotice: true };
}
