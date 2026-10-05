const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync('src/dailymix/sources/publish.js', 'utf8'), context);
const publish = context.publish;
const marker = 'dailymix:managed:v1';

function fake(initial = null, failure = null) {
    let mutations = 0;
    let identifier = 0;
    const playlists = [];
    const library = ['a', 'b', 'c'].map(id => ({id, library: true}));
    library.persistentID = () => library.map(t => t.id);
    function playlist(name, ids, description = marker) {
        const result = {name, identifier: String(++identifier), tracks: []};
        result.tracks.persistentID = () => result.tracks.map(t => t.id);
        result.smart = () => false;
        result.genius = () => false;
        result.description = () => description;
        result.persistentID = () => result.identifier;
        ids.forEach(id => result.tracks.push({id, parent: result}));
        return result;
    }
    if (initial) playlists.push(playlist('Daily Mix', initial.ids, initial.description));
    const userPlaylists = {
        whose: ({name}) => () => playlists.filter(p => p.name === name),
        push: p => { mutations++; playlists.push(p); }
    };
    let failed = false;
    const music = {
        libraryPlaylists: [{fileTracks: library}], userPlaylists,
        UserPlaylist: properties => playlist(properties.name, [], properties.description),
        duplicate: (track, {to}) => {
            mutations++;
            if (!failed && failure && failure(to, track)) { failed = true; throw Error('injected failure'); }
            to.tracks.push({id: track.id, parent: to});
        },
        delete: element => {
            mutations++;
            assert.ok(!element.library, 'must never delete a library track');
            if (element.parent) element.parent.tracks.splice(element.parent.tracks.indexOf(element), 1);
            else playlists.splice(playlists.indexOf(element), 1);
        }
    };
    return {music, playlists, library, mutations: () => mutations};
}
function request(f, overrides = {}) {
    const target = f.playlists[0];
    return {name: 'Daily Mix', ids: ['b', 'c'], dry_run: false,
            expected_playlist_id: target ? target.persistentID() : null,
            expected_previous_ids: target ? Array.from(target.tracks.persistentID()) : [],
            staging_name: 'staging', ...overrides};
}
let f = fake();
let plan = publish(f.music, request(f, {dry_run: true}));
assert.equal(plan.action, 'create');
assert.equal(f.mutations(), 0);
let result = publish(f.music, request(f));
assert.equal(result.verified, true);
assert.deepEqual(f.playlists[0].tracks.persistentID(), ['b', 'c']);
assert.equal(f.playlists[0].name, 'Daily Mix');
assert.equal(f.library.length, 3);
let before = f.mutations();
assert.equal(publish(f.music, request(f)).action, 'unchanged');
assert.equal(f.mutations(), before);

f = fake({ids: ['a']});
const targetId = f.playlists[0].persistentID();
publish(f.music, request(f));
assert.equal(f.playlists.length, 1);
assert.equal(f.playlists[0].persistentID(), targetId);
assert.deepEqual(f.playlists[0].tracks.persistentID(), ['b', 'c']);

f = fake({ids: ['a']}, target => target.name === 'Daily Mix');
assert.throws(() => publish(f.music, request(f)), /injected failure/);
assert.deepEqual(f.playlists[0].tracks.persistentID(), ['a']);
assert.equal(f.playlists.length, 1);
assert.equal(f.library.length, 3);

f = fake({ids: ['a']}, target => target.name === 'staging');
assert.throws(() => publish(f.music, request(f)), /injected failure/);
assert.deepEqual(f.playlists[0].tracks.persistentID(), ['a']);
assert.equal(f.playlists.length, 1);

f = fake({ids: ['a'], description: 'My own playlist'});
assert.throws(() => publish(f.music, request(f)), /not a Daily Mix managed/);
assert.equal(f.mutations(), 0);
f = fake();
assert.throws(() => publish(f.music, request(f, {ids: ['missing']})), /no longer in library/);
assert.equal(f.mutations(), 0);
f = fake({ids: ['a']});
assert.throws(() => publish(f.music, request(f, {expected_previous_ids: []})), /changed since preflight/);
assert.equal(f.mutations(), 0);
console.log('Publisher bridge: preflight, creation, replacement, retry, rollback, staging failure, ownership, and missing-track checks passed');
