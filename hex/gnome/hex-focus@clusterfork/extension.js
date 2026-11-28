import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const PATH = '/io/github/KianBahasadri/Clusterfork/HexFocus';
const WORKER_UNIT = '/org/freedesktop/systemd1/unit/hex_2dlinear_2dissue_2eservice';
const MAX_TOASTS = 8;
const MAX_ID_LEN = 64;
const MAX_LABEL_LEN = 46;
const MAX_STEPS = 6;
const TOAST_WIDTH = 276;
const TOAST_MARGIN = 16;
const DONE_DISMISS_MS = 1500;
const ERROR_DISMISS_MS = 3500;
const STALE_TTL_SECONDS = 1260;
const TOAST_STATES = new Set(['active', 'done', 'error']);
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
    <method name="Progress">
      <arg type="s" direction="in"/><arg type="u" direction="in"/>
      <arg type="u" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
    </method>
    <method name="ProgressClear"><arg type="s" direction="in"/></method>
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
        this._toasts = new Map();
        this._toastWatch = 0;
        this._toastOwner = null;
        this._toastBox = null;
        this._focus = global.display.connect('notify::focus-window', () => {
            this._revision = (this._revision + 1) >>> 0;
            this._release();
        });
        this._sessionSignal = Main.sessionMode.connect('updated', () => this._syncToastVisibility());
        this._monitorsSignal = Main.layoutManager.connect('monitors-changed', () => this._positionToasts());
        this._workareasSignal = global.display.connect('workareas-changed', () => this._positionToasts());
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
        if (this._sessionSignal)
            Main.sessionMode.disconnect(this._sessionSignal);
        if (this._monitorsSignal)
            Main.layoutManager.disconnect(this._monitorsSignal);
        if (this._workareasSignal)
            global.display.disconnect(this._workareasSignal);
        this._clearAllToasts(true);
        this._release();
        this._keyboard = this._seat = null;
        this._focus = this._sessionSignal = this._monitorsSignal = this._workareasSignal = 0;
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
            case 'Progress':
                this._setProgress(sender, ...parameters.deepUnpack());
                invocation.return_value(null);
                break;
            case 'ProgressClear':
                this._clearProgress(...parameters.deepUnpack());
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
        let blocked = modifiers.SHIFT_MASK | modifiers.CONTROL_MASK | modifiers.MOD1_MASK |
            modifiers.MOD3_MASK | modifiers.MOD4_MASK | modifiers.MOD5_MASK |
            modifiers.SUPER_MASK | modifiers.HYPER_MASK | modifiers.META_MASK;
        if (this._pressed.has(Clutter.KEY_Shift_L))
            blocked &= ~modifiers.SHIFT_MASK;
        if (this._pressed.has(Clutter.KEY_Control_L))
            blocked &= ~modifiers.CONTROL_MASK;
        return !(global.get_pointer()[2] & blocked);
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
            if (!this._ready()) {
                this._release();
                return false;
            }
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

    _sanitizeText(value, limit) {
        return String(value ?? '').replace(/[\x00-\x1f\x7f]+/g, ' ').trim().slice(0, limit);
    }

    _ensureToastBox() {
        if (this._toastBox)
            return;
        this._toastBox = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            reactive: false,
            can_focus: false,
            style: 'spacing: 8px;',
        });
        this._toastBox.connect('notify::height', () => this._positionToasts());
        this._toastBox.connect('notify::width', () => this._positionToasts());
        Main.layoutManager.addChrome(this._toastBox, {affectsInputRegion: false, trackFullscreen: false});
    }

    _syncToastVisibility() {
        if (this._toastBox)
            this._toastBox.visible = !Main.sessionMode.isLocked && (this._toasts?.size ?? 0) > 0;
    }

    _positionToasts() {
        if (!this._toastBox || !(this._toasts?.size ?? 0))
            return;
        const area = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
        if (!area)
            return;
        const [, , natWidth, natHeight] = this._toastBox.get_preferred_size();
        const width = Math.max(Math.ceil(natWidth || 0), TOAST_WIDTH);
        const height = Math.ceil(natHeight || 0);
        const x = Math.max(area.x, Math.round(area.x + area.width - width - TOAST_MARGIN));
        const y = Math.max(area.y, Math.round(area.y + area.height - height - TOAST_MARGIN));
        this._toastBox.set_position(x, y);
    }

    _watchToastOwner(sender) {
        if (this._toastOwner === sender)
            return;
        if (this._toastWatch)
            Gio.bus_unwatch_name(this._toastWatch);
        this._toastOwner = sender;
        this._toastWatch = Gio.bus_watch_name_on_connection(Gio.DBus.session, sender,
            Gio.BusNameWatcherFlags.NONE, null, () => this._clearAllToasts(false));
    }

    _clearToastTimers(entry) {
        if (entry.dismissId) {
            GLib.Source.remove(entry.dismissId);
            entry.dismissId = 0;
        }
        if (entry.ttlId) {
            GLib.Source.remove(entry.ttlId);
            entry.ttlId = 0;
        }
    }

    _removeToast(id) {
        const entry = this._toasts?.get(id);
        if (!entry)
            return;
        this._clearToastTimers(entry);
        entry.card.destroy();
        this._toasts.delete(id);
        this._syncToastVisibility();
        this._positionToasts();
    }

    _setProgress(sender, rawId, rawStep, rawTotal, rawLabel, rawState) {
        this._toasts ??= new Map();
        const id = this._sanitizeText(rawId, MAX_ID_LEN);
        if (!id)
            return;
        const total = Math.max(1, Math.min(MAX_STEPS, Number(rawTotal) || 1));
        const step = Math.max(0, Math.min(total, Number(rawStep) || 0));
        const state = TOAST_STATES.has(rawState) ? rawState : 'active';
        const label = this._sanitizeText(rawLabel, MAX_LABEL_LEN) || 'Creating Linear issue';

        this._watchToastOwner(sender);
        this._ensureToastBox();

        let entry = this._toasts.get(id);
        if (!entry) {
            if (this._toasts.size >= MAX_TOASTS)
                this._removeToast(this._toasts.keys().next().value);
            const card = new St.BoxLayout({
                orientation: Clutter.Orientation.VERTICAL,
                reactive: false,
                can_focus: false,
                style: 'width: 276px; padding: 10px 12px; spacing: 7px; border-radius: 10px; ' +
                    'background-color: rgba(24, 24, 28, 0.92); border: 1px solid rgba(255, 255, 255, 0.14);',
            });
            const textLabel = new St.Label({
                text: label,
                style: 'font-size: 12px; font-weight: 600; color: rgba(255, 255, 255, 0.92);',
            });
            const bar = new St.BoxLayout({
                orientation: Clutter.Orientation.HORIZONTAL,
                reactive: false,
                can_focus: false,
                style: 'spacing: 4px;',
            });
            card.add_child(textLabel);
            card.add_child(bar);
            this._toastBox.insert_child_at_index(card, 0);
            entry = {card, textLabel, bar, dismissId: 0, ttlId: 0};
            this._toasts.set(id, entry);
        }

        entry.textLabel.text = label;
        entry.bar.destroy_all_children();
        const filled = state === 'done' ? total : step;
        const fill = state === 'done' ? '#33d17a' : state === 'error' ? '#e01b24' : '#62a0ea';
        for (let index = 0; index < total; index++) {
            const color = index < filled ? fill : 'rgba(255, 255, 255, 0.18)';
            entry.bar.add_child(new St.Widget({
                x_expand: true,
                style: `height: 5px; border-radius: 3px; background-color: ${color};`,
            }));
        }

        this._clearToastTimers(entry);
        if (state === 'done' || state === 'error') {
            const delay = state === 'done' ? DONE_DISMISS_MS : ERROR_DISMISS_MS;
            entry.dismissId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
                entry.dismissId = 0;
                this._removeToast(id);
                return GLib.SOURCE_REMOVE;
            });
        } else {
            entry.ttlId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, STALE_TTL_SECONDS, () => {
                entry.ttlId = 0;
                this._removeToast(id);
                return GLib.SOURCE_REMOVE;
            });
        }

        this._syncToastVisibility();
        this._positionToasts();
    }

    _clearProgress(rawId) {
        if (!rawId) {
            this._clearAllToasts(false);
            return;
        }
        const id = this._sanitizeText(rawId, MAX_ID_LEN);
        if (id)
            this._removeToast(id);
    }

    _clearAllToasts(destroyBox) {
        for (const entry of this._toasts?.values() ?? []) {
            this._clearToastTimers(entry);
            entry.card.destroy();
        }
        this._toasts?.clear();
        if (this._toastWatch)
            Gio.bus_unwatch_name(this._toastWatch);
        this._toastWatch = 0;
        this._toastOwner = null;
        if (destroyBox && this._toastBox) {
            Main.layoutManager.removeChrome(this._toastBox);
            this._toastBox.destroy();
            this._toastBox = null;
        } else {
            this._syncToastVisibility();
        }
    }
}
