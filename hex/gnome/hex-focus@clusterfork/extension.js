import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const PATH = '/io/github/KianBahasadri/Clusterfork/HexFocus';
const INTERFACE = `<node>
  <interface name="io.github.KianBahasadri.Clusterfork.HexFocus">
    <method name="Get">
      <arg type="u" direction="out"/><arg type="u" direction="out"/>
      <arg type="i" direction="out"/><arg type="s" direction="out"/>
    </method>
    <method name="Activate"><arg type="u" direction="in"/><arg type="b" direction="out"/></method>
    <method name="Ready"><arg type="b" direction="out"/></method>
    <method name="Key">
      <arg type="u" direction="in"/><arg type="u" direction="in"/>
      <arg type="u" direction="in"/><arg type="b" direction="in"/><arg type="b" direction="out"/>
    </method>
    <method name="End"/>
  </interface>
</node>`;

export default class HexFocus extends Extension {
    enable() {
        this._revision = 0;
        this._seat = global.stage.context.get_backend().get_default_seat();
        this._keyboard = this._seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._pressed = new Map();
        this._watch = 0;
        this._owner = null;
        this._focus = global.display.connect('notify::focus-window', () => {
            this._revision = (this._revision + 1) >>> 0;
            this._release();
        });
        this._export = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._export.export(Gio.DBus.session, PATH);
    }

    disable() {
        this._export?.unexport();
        if (this._focus)
            global.display.disconnect(this._focus);
        this._release();
        this._keyboard = this._seat = this._export = null;
        this._focus = 0;
    }

    Get() {
        const window = this._acceptsKeys() ? global.display.focus_window : null;
        return [window?.get_stable_sequence() ?? 0, this._revision,
            window?.get_pid() ?? 0, window?.get_title() ?? ''];
    }

    Activate(sequence) {
        const window = global.display.list_all_windows().find(item => item.get_stable_sequence() === sequence);
        if (!window || Main.sessionMode.isLocked)
            return false;
        if (window !== global.display.focus_window)
            Main.activateWindow(window);
        return true;
    }

    Ready() {
        const modifiers = Clutter.ModifierType;
        const held = modifiers.SHIFT_MASK | modifiers.CONTROL_MASK | modifiers.MOD1_MASK |
            modifiers.SUPER_MASK | modifiers.META_MASK;
        return !(global.get_pointer()[2] & held);
    }

    _acceptsKeys() {
        return !Main.sessionMode.isLocked && !global.stage.get_grab_actor() &&
            !global.stage.get_key_focus() && !Main.overview.visible;
    }

    _key(sym, down) {
        this._keyboard.notify_keyval(Clutter.CURRENT_TIME, sym,
            down ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
    }

    _send(sender, sequence, revision, sym, down) {
        if (!this._acceptsKeys() || revision !== this._revision ||
            global.display.focus_window?.get_stable_sequence() !== sequence)
            return false;
        if (this._owner !== sender) {
            this._release();
            this._owner = sender;
            this._watch = Gio.bus_watch_name_on_connection(Gio.DBus.session, sender,
                Gio.BusNameWatcherFlags.NONE, null, () => this._release());
        }
        if (down) {
            let actual = sym;
            const letter = (sym >= Clutter.KEY_a && sym <= Clutter.KEY_z) ||
                (sym >= Clutter.KEY_A && sym <= Clutter.KEY_Z);
            if (letter && this._seat.get_keymap().get_caps_lock_state())
                actual = sym ^ 32;
            this._pressed.set(sym, actual);
            this._key(actual, true);
        } else if (this._pressed.has(sym)) {
            this._key(this._pressed.get(sym), false);
            this._pressed.delete(sym);
        }
        return true;
    }

    KeyAsync(args, invocation) {
        const accepted = this._send(invocation.get_sender(), ...args);
        invocation.return_value(new GLib.Variant('(b)', [accepted]));
    }

    EndAsync(_args, invocation) {
        if (this._owner === invocation.get_sender())
            this._release();
        invocation.return_value(null);
    }

    _release() {
        for (const sym of this._pressed?.values() ?? [])
            this._key(sym, false);
        this._pressed?.clear();
        if (this._watch)
            Gio.bus_unwatch_name(this._watch);
        this._watch = 0;
        this._owner = null;
    }
}
