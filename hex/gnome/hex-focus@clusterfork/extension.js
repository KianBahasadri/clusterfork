import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const PATH = '/io/github/KianBahasadri/Clusterfork/HexFocus';
const WORKER_UNIT = '/org/freedesktop/systemd1/unit/hex_2dlinear_2dissue_2eservice';
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

function readReply(connection, name, path, iface, method, parameters, replyType) {
    return new Promise((resolve, reject) => {
        connection.call(name, path, iface, method, parameters, new GLib.VariantType(replyType),
            Gio.DBusCallFlags.NO_AUTO_START, 200, null, (bus, result) => {
                try {
                    resolve(bus.call_finish(result).deepUnpack()[0]);
                } catch (error) {
                    reject(error);
                }
            });
    });
}

async function isHexWorker(connection, sender) {
    const [caller, worker] = await Promise.all([
        readReply(connection, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionUnixProcessID', new GLib.Variant('(s)', [sender]), '(u)'),
        readReply(connection, 'org.freedesktop.systemd1', WORKER_UNIT,
            'org.freedesktop.DBus.Properties', 'Get',
            new GLib.Variant('(ss)', ['org.freedesktop.systemd1.Service', 'MainPID']), '(v)'),
    ]);
    return caller > 0 && caller === worker.deepUnpack();
}

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
        const info = Gio.DBusNodeInfo.new_for_xml(INTERFACE);
        this._registration = Gio.DBus.session.register_object(PATH, info.interfaces[0],
            (connection, sender, _path, _iface, method, parameters, invocation) => {
                void this._handleCall(connection, sender, method, parameters, invocation);
            }, null, null);
    }

    disable() {
        if (this._registration)
            Gio.DBus.session.unregister_object(this._registration);
        this._registration = 0;
        if (this._focus)
            global.display.disconnect(this._focus);
        this._release();
        this._keyboard = this._seat = null;
        this._focus = 0;
    }

    async _handleCall(connection, sender, method, parameters, invocation) {
        const registration = this._registration;
        const allowed = await isHexWorker(connection, sender).catch(() => false);
        if (!allowed || !registration || registration !== this._registration) {
            invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied',
                'Hex Typing Focus is available to the Hex shortcut service.');
            return;
        }

        try {
            switch (method) {
            case 'Get':
                invocation.return_value(new GLib.Variant('(uuis)', this._windowInfo()));
                break;
            case 'Activate':
                invocation.return_value(new GLib.Variant('(b)', [this._activate(...parameters.deepUnpack())]));
                break;
            case 'Ready':
                invocation.return_value(new GLib.Variant('(b)', [this._ready()]));
                break;
            case 'Key':
                invocation.return_value(new GLib.Variant('(b)', [this._send(sender, ...parameters.deepUnpack())]));
                break;
            case 'End':
                if (this._owner === sender)
                    this._release();
                invocation.return_value(null);
                break;
            }
        } catch (error) {
            logError(error, 'Hex Typing Focus');
            invocation.return_dbus_error('org.freedesktop.DBus.Error.Failed', 'Hex Typing Focus could not handle the request.');
        }
    }

    _windowInfo() {
        const window = this._acceptsKeys() ? global.display.focus_window : null;
        return [window?.get_stable_sequence() ?? 0, this._revision,
            window?.get_pid() ?? 0, window?.get_title() ?? ''];
    }

    _activate(sequence) {
        const window = global.display.list_all_windows().find(item => item.get_stable_sequence() === sequence);
        if (!window || Main.sessionMode.isLocked)
            return false;
        if (window !== global.display.focus_window)
            Main.activateWindow(window);
        return true;
    }

    _ready() {
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
