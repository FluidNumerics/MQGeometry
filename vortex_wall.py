"""Example of a one layer QG model"""
import numpy as np
import torch

from MQGeometry.helmholtz import solve_helmholtz_dst, solve_helmholtz_dst_cmm
from MQGeometry.qgm import QGFV

torch.backends.cudnn.deterministic = True
device = 'cuda' if torch.cuda.is_available() else 'cpu'
dtype = torch.float64


# grid
# nx = 1536
# ny = 1536
nx = 1024
ny = 1024
# nx = 512
# ny = 512
# nx = 256
# ny = 256
# nx = 128
# ny = 128
nl = 1
L = 100000
dx = L / nx
dy = L / ny
xv = torch.linspace(-L/2, L/2, nx+1, dtype=torch.float64, device=device)
yv = torch.linspace(-L/2, L/2, ny+1, dtype=torch.float64, device=device)
x, y = torch.meshgrid(xv, yv, indexing='ij')

H = torch.zeros(nl,1,1, dtype=dtype, device=device)
if nl == 1:
    H[0,0,0] = 1000.

# density/gravity
g_prime = torch.zeros(nl,1,1, dtype=dtype, device=device)
if nl == 1:
    g_prime[0,0,0] = 10

## create rankine vortex
# Burger and Rossby numbers
Bu = 1
Ro = 0.01

r0 = L / 16

# coriolis set with Bu Number
f0 = torch.sqrt(g_prime[0,0,0] * H[0,0,0] / Bu / r0**2)
beta = 0
f = f0 + beta * (y - L/2)

# wind forcing, bottom drag
tau0 = 0.
bottom_drag_coef = 0.

apply_mask = True

xc = 0.5 * (xv[1:] + xv[:-1])
yc = 0.5 * (yv[1:] + yv[:-1])
x, y = torch.meshgrid(xc, yc, indexing='ij')
x_vor, y_vor = -L//4, -6*L//14
r = torch.sqrt((x-x_vor)**2 + (y-y_vor)**2)
soft_step = lambda x: torch.sigmoid(x/100)
pv = soft_step(r0 - r)
pv /= pv.mean()

mask = torch.ones_like(x)
mask[nx//2:nx//2+2,:ny//4] = 0

param = {
    'nx': nx,
    'ny': ny,
    'nl': nl,
    'mask': mask,
    'n_ens': 1,
    'Lx': L,
    'Ly': L,
    'flux_stencil': 5,
    'H': H,
    'g_prime': g_prime,
    'tau0': tau0,
    'f0': f0,
    'beta': beta,
    'bottom_drag_coef': bottom_drag_coef,
    'device': device,
    'dt': 0, # time-step (s)
}


qg = QGFV(param)
qg.q = pv.unsqueeze(0).unsqueeze(0)
# compute p from q_over_f0
q_i = qg.interp_TP(qg.q)
helmholtz_rhs = torch.einsum('lm,...mxy->...lxy', qg.Cl2m, q_i)
if apply_mask:
    psi_modes = solve_helmholtz_dst_cmm(
                helmholtz_rhs*qg.masks.psi[...,1:-1,1:-1],
                qg.helmholtz_dst, qg.cap_matrices,
                qg.masks.psi_irrbound_xids,
                qg.masks.psi_irrbound_yids,
                qg.masks.psi)
else:
    psi_modes = solve_helmholtz_dst(helmholtz_rhs, qg.helmholtz_dst)
qg.psi = torch.einsum('lm,...mxy->...lxy', qg.Cm2l, psi_modes)

# set amplitude to have correct Rossby number
u, v = qg.grad_perp(qg.psi, qg.dx, qg.dy)
u_norm_max = max(torch.abs(u).max().item(), torch.abs(v).max().item())
factor = Ro * f0 * r0 / u_norm_max
qg.psi *= factor
qg.q *= factor

u, v = qg.grad_perp(qg.psi, qg.dx, qg.dy)
u_max = u.max().cpu().item()
v_max = v.max().cpu().item()
print(f'u_max {u_max:.2e}, v_max {v_max:.2e}')

cfl = 0.3
dt = cfl * min(dx / u_max, dy / v_max)


qg.dt = dt


# time params
t = 0

w_0 = qg.laplacian_h(qg.psi, qg.dx, qg.dy).squeeze()
tau = 1. / torch.sqrt(w_0.pow(2).mean()).cpu().item()
print(f'tau = {tau *f0:.2f} f0-1')
t_end = 22. * tau
freq_plot = int(t_end / 20  / dt) + 1
freq_checknan = 10
freq_log = 200
n_steps = int(t_end / dt) + 1


if freq_plot > 0:
    ims = []
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.rcParams.update({'font.size': 18})

    palette = plt.cm.Reds.with_extremes(bad='grey')
    plt.ion()
    f,a = plt.subplots(1, 2, figsize=(16,9))
    a[0].set_title('q (units of $f_0$)')
    a[1].set_title('$\\psi$ $(m^2 s^{-1})$')
    a[0].set_xticks([]), a[0].set_yticks([])
    a[1].set_xticks([]), a[1].set_yticks([])
    plt.tight_layout()
    plt.pause(0.1)
    ts_plot = np.linspace(0, t_end, 8)
    ns_plot = [int(t / dt)+1 for t in ts_plot]
    f2,a2 = plt.subplots(2, 4, figsize=(16,9))
    f2.suptitle('Evolution of the potential vorticity q (units of $f_0$) and stream function contours')

wM, pM = None, None

import time
t0 = time.time()
for n in range(1, n_steps+1):
    if n in ns_plot:
        ind = ns_plot.index(n)
        q_over_f0 = (qg.q / qg.f0 * qg.masks.q)[0,0].cpu().numpy()
        q_over_f0 = np.ma.masked_where((1-qg.masks.q[0,0].cpu().numpy()), q_over_f0)
        if wM is None:
            wM = np.abs(q_over_f0).max()
        i, j = ind//4, ind % 4
        im = a2[i,j].imshow(q_over_f0.T, cmap=palette, origin='lower', vmin=0, vmax=wM, animated=True)
        psi = qg.psi.cpu().numpy()[0,0]
        eps = 5e-2 * (psi.max() - psi.min())
        a2[i,j].contour(psi.T, colors='grey', origin='lower', levels=np.linspace(psi.min()+eps, psi.max()-eps, 6))
        a2[i,j].set_title(f't={t/tau:.1f}$\\tau$')
        a2[i,j].set_xticks([]), a2[i,j].set_yticks([])
        plt.pause(0.05)

    if freq_plot > 0 and (n % freq_plot == 0 or n == n_steps):
        q_over_f0 = (qg.q / qg.f0 * qg.masks.q)[0,0].cpu().numpy()
        q_over_f0 = np.ma.masked_where((1-qg.masks.q[0,0].cpu().numpy()), q_over_f0)
        psi = qg.psi.cpu().numpy()
        if wM is None or pM is None:
            wM = np.abs(q_over_f0).max()
            pM = np.abs(psi).max()
        im0 = a[0].imshow(q_over_f0.T, cmap=palette, origin='lower', vmin=-wM, vmax=wM, animated=True)
        im1 = a[1].imshow(psi[0,0].T, cmap='bwr', origin='lower', vmin=-pM, vmax=pM, animated=True)
        if n // freq_plot == 1:
            f.colorbar(im0, ax=a[0])
            f.colorbar(im1, ax=a[1])
        f.suptitle(f'Ro={Ro:.2f}, Bu={Bu:.2f}, t={t/tau:.2f}$\\tau$')
        plt.pause(0.05)

    qg.step()
    t += dt

    if n % freq_checknan == 0 and torch.isnan(qg.psi).any():
        raise ValueError(f'Stopping, NAN number in psi at iteration {n}.')


    if freq_log > 0 and n % freq_log == 0:
        u, v = qg.grad_perp(qg.psi, qg.dx, qg.dy)
        u, v = u.cpu().numpy(), v.cpu().numpy()
        q = qg.q.cpu().numpy()
        log_str = f'{n=:06d}, qg t={t/tau:.2f} tau, ' \
                    f'u: {u.mean():+.1E}, {np.abs(u).max():.1E}, ' \
                    f'v: {v.mean():+.1E}, {np.abs(v).max():.2E}, ' \
                    f'q min: {q.min():+.3E}'
        print(log_str)

f2.tight_layout()
f2.subplots_adjust(right=0.91)
cbar_ax = f2.add_axes([0.92, 0.04, 0.02, 0.9])
f2.colorbar(im, cax=cbar_ax)

total_time = time.time() - t0
print(f'{total_time // 60}min {(total_time % 60)} sec')
