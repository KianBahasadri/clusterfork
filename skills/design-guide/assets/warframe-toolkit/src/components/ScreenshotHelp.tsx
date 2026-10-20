import type { ScreenshotSource } from '../lib/matching';
import { Dialog } from './Dialog';

const inventoryExample = 'images/import/inventory-weapons.webp';
const codexExample = 'images/import/codex-weapons.webp';

export function ScreenshotHelp({ source, onClose }: { source: ScreenshotSource; onClose: () => void }) {
  return <Dialog title={source === 'relics' ? 'Relic screenshots' : source === 'inventory' ? 'Inventory screenshots' : 'Codex screenshots'} onClose={onClose} className="screenshot-help-dialog">
    {source === 'relics' ? <ol className="screenshot-instructions">
      <li><strong>Open Void Relics / Refinement.</strong><p>Use English text. Capture complete relic names, refinement labels, and the quantity in each upper-left corner.</p></li>
      <li><strong>Capture all rows.</strong><p>Scroll with one row overlapping, keeping whole entries visible. Drop, paste, or choose up to 10 screenshots here.</p></li>
      <li><strong>Review every stack.</strong><p>Check names, refinements, and quantities. When no count is read, 1 is suggested and left unchecked. Missing or conflicting fields need confirmation. Overlapping screenshots never add counts together.</p></li>
      <li><strong>Import selected stacks.</strong><p>Selected quantities replace their saved values. Notes, equipment, and relics missing from the screenshots stay unchanged. Images remain in this browser.</p></li>
    </ol> : source === 'inventory' ? <>
      <ol className="screenshot-instructions">
        <li><strong>Open your weapon inventory.</strong><p>In your Orbiter, open the pause menu, then <b>Equipment → Inventory</b>. Select the <b>Weapons</b> tab with the gun icon.</p></li>
        <li><strong>Show the weapons you own.</strong><p>Clear the search field and leave <b>Only Sellable</b> unchecked. You should see the weapon grid below. Stay on this screen; taking screenshots does not require selecting or selling anything.</p></li>
      </ol>
      <figure className="screenshot-example">
        <img src={inventoryExample} width={1920} height={1200} alt="Warframe Inventory/Sell screen with the Weapons tab selected, showing Tonkor, Boltor, and Braton Prime with rank labels. The bottom row is cut off." />
        <figcaption><span>Inventory → Weapons. Capture the cut-off bottom row in your next screenshot.</span><a href={inventoryExample} target="_blank" rel="noreferrer">Open full-size example</a></figcaption>
      </figure>
      <ol className="screenshot-instructions" start={3}>
        <li><strong>Capture the visible rows.</strong><p>Use your computer’s screenshot tool with the game’s text in English. Keep the full weapon names, variant names, and rank labels visible. Save the original image as PNG, JPG, or WebP.</p></li>
        <li><strong>Scroll and repeat.</strong><p>Scroll down with one row overlapping the previous screenshot, then capture again. Continue to the bottom of the list. Overlapping weapons are merged during review.</p></li>
        <li><strong>Add the images here.</strong><p>Return to the <b>Inventory</b> section. Drop your screenshots onto its upload area or click it to choose files. To paste an image, highlight Inventory by moving the pointer to its side of the page or focusing its upload area. The first image hides the other section. Use <b>Add screenshots</b> to add more to the same batch, including while reading is in progress. Add up to 10 images per batch, each below 20 MB and 32 megapixels.</p></li>
        <li><strong>Review before importing.</strong><p>Compare names, variants, and ranks with your screenshots. Reaching a weapon’s maximum rank checks <b>Mastered</b>: usually 30, or 40 for weapons such as Paracesis. Modular weapons and Amps need mastery review. Correct or deselect incorrect matches, then click <b>Import</b>. Previously recorded mastery is preserved even if the current rank is lower.</p></li>
      </ol>
    </> : <>
    <ol className="screenshot-instructions">
      <li><strong>Open the Codex weapon list.</strong><p>In your Orbiter, open the <b>Codex</b> and its weapon collection. Clear the search and select <b>All</b> to show the grid below.</p></li>
      <li><strong>Keep names and mastery badges visible.</strong><p>Use the game’s English text. Keep each complete name strip and the upper-right mastery badge visible. Scroll with one row overlapping between screenshots.</p></li>
    </ol>
    <figure className="screenshot-example">
      <img src={codexExample} width={1920} height={1200} alt="Warframe Codex weapon grid with 32 readable names, each with a mastery laurel above and to the right." />
      <figcaption><span>Codex → Weapons. Keep each weapon’s name, image, and mastery badge visible.</span><a href={codexExample} target="_blank" rel="noreferrer">Open full-size example</a></figcaption>
    </figure>
    <ol className="screenshot-instructions" start={3}>
      <li><strong>Add the images to Codex.</strong><p>Drop or choose your images in the <b>Codex</b> section. To paste an image, highlight Codex by moving the pointer to its side of the page or focusing its upload area. The first image hides the other section. Use <b>Add screenshots</b> to keep adding to the same batch. Use PNG, JPG, or WebP, up to 10 images per batch, each below 20 MB and 32 megapixels.</p></li>
      <li><strong>Review mastery and ownership.</strong><p><b>Mastered</b> is checked when the reader recognizes a mastery laurel for that weapon. This grid does not establish current rank or ownership: <b>Owned</b> starts unchecked and rank stays blank. Check ownership only for equipment you currently own. Review all matches before importing; missing badges never clear saved mastery, and blank ranks preserve saved ranks.</p></li>
    </ol>
    </>}
    <div className="dialog-footer"><button type="button" className="button secondary" onClick={onClose}>Close instructions</button></div>
  </Dialog>;
}
