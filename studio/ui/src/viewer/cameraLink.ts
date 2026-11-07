// One camera shared by several 3D views (a comparison's panels, Principle VII): turning or zooming any view moves
// the others to the same pose.

import type { CameraPose } from '../organise';

type Listener = (pose: CameraPose, source: object) => void;

export class CameraLink {
  pose: CameraPose;
  private readonly listeners = new Set<Listener>();

  constructor(initial: CameraPose) {
    this.pose = initial;
  }

  /** A view moved: every other view follows. `source` is the moving view's own token. */
  publish(pose: CameraPose, source: object): void {
    this.pose = pose;
    for (const listener of this.listeners) listener(pose, source);
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}
