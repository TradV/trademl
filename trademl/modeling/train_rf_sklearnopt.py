# fundamental modules
import numpy as np
import pandas as pd
from numba import njit
import matplotlib.pyplot as plt
import matplotlib
import joblib
import json
import sys
import os
# preprocessing
import sklearn
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, BaggingClassifier
from sklearn.model_selection import GridSearchCV
from mlfinlab.ensemble import SequentiallyBootstrappedBaggingClassifier
from sklearn.base import clone
import xgboost
import shap
# metrics 
import mlfinlab as ml
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    accuracy_score,
    roc_curve,
    log_loss,
    )
from boruta import BorutaPy
# finance packages
import trademl as tml
# import vectorbt as vbt


if __name__ == '__main__':

    ### DON'T SHOW GRAPH OPTION
    matplotlib.use("Agg")


    ### GLOBALS
    DATA_PATH = 'D:/market_data/usa/ohlcv_features/'


    ### IMPORT DATA
    contract = ['SPY']
    with pd.HDFStore(DATA_PATH + contract[0] + '.h5') as store:
        data = store.get(contract[0])
    data.sort_index(inplace=True)


    ### CHOOSE/REMOVE VARIABLES
    remove_ohl = ['open', 'low', 'high', 'average', 'barCount',
                # 'vixFirst', 'vixHigh', 'vixLow', 'vixClose', 'vixVolume',
                'open_orig', 'high_orig', 'low_orig']
    remove_ohl = [col for col in remove_ohl if col in data.columns]
    data.drop(columns=remove_ohl, inplace=True)  #correlated with close


    ### NON-MODEL HYPERPARAMETERS
    num_threads = 1
    structural_break_regime = 'all'
    labeling_technique = 'trend_scanning'
    std_outlier = 10
    tb_volatility_lookback = 500
    tb_volatility_scaler = 1
    tb_triplebar_num_days = 10
    tb_triplebar_pt_sl = [1, 1]
    tb_triplebar_min_ret = 0.004
    ts_look_forward_window = 1200  # 60 * 8 * 10 (10 days)
    ts_min_sample_length = 30
    ts_step = 5
    tb_min_pct = 0.10
    sample_weights_type = 'returns'
    cv_type = 'purged_kfold'
    cv_number = 4
    rand_state = 3
    stationary_close_lables = False

    ### MODEL HYPERPARAMETERS
    # max_depth = 3
    # max_features = 20
    # n_estimators = 500

    ### POSTMODEL PARAMETERS
    keep_important_features = 25
    # vectorbt_slippage = 0.0015
    # vectorbt_fees = 0.0015


    ### REGIME DEPENDENT ANALYSIS
    if structural_break_regime == 'chow':
        if (data.loc[data['chow_segment'] == 1].shape[0] / 60 / 8) < 365:
            data = data.iloc[-(60*8*365):]
        else:
            data = data.loc[data['chow_segment'] == 1]

    ### USE STATIONARY CLOSE TO CALCULATE LABELS
    if stationary_close_lables:
        data['close_orig'] = data['close']  # with original close reslts are pretty bad!


    ### REMOVE OUTLIERS
    # outlier_remove = tml.modeling.pipelines.OutlierStdRemove(std_outlier)
    # data = outlier_remove.fit_transform(data)


    ### LABELING
    if labeling_technique == 'triple_barrier':
        # TRIPLE BARRIER LABELING
        triple_barrier_pipe= tml.modeling.pipelines.TripleBarierLabeling(
            close_name='close_orig',
            volatility_lookback=tb_volatility_lookback,
            volatility_scaler=tb_volatility_scaler,
            triplebar_num_days=tb_triplebar_num_days,
            triplebar_pt_sl=tb_triplebar_pt_sl,
            triplebar_min_ret=tb_triplebar_min_ret,
            num_threads=num_threads,
            tb_min_pct=tb_min_pct
        )
        tb_fit = triple_barrier_pipe.fit(data)
        labeling_info = tb_fit.triple_barrier_info
        X = tb_fit.transform(data)
    elif labeling_technique == 'trend_scanning':
        trend_scanning_pipe = tml.modeling.pipelines.TrendScanning(
            close_name='close_orig',
            volatility_lookback=tb_volatility_lookback,
            volatility_scaler=tb_volatility_scaler,
            ts_look_forward_window=ts_look_forward_window,
            ts_min_sample_length=ts_min_sample_length,
            ts_step=ts_step
            )
        labeling_info = trend_scanning_pipe.fit(data)
        X = trend_scanning_pipe.transform(data)
    elif labeling_technique == 'fixed_horizon':
        X = data.copy()
        labeling_info = ml.labeling.fixed_time_horizon(data['close_orig'], threshold=0.005, resample_by='B').dropna().to_frame()
        labeling_info = labeling_info.rename(columns={'close_orig': 'bin'})
        print(labeling_info.iloc[:, 0].value_counts())
        X = X.iloc[:-1, :]


    ### CLUSTERED FEATURES
    # feat_subs = ml.clustering.feature_clusters.get_feature_clusters(
    #     X, dependence_metric='information_variation',
    #     distance_metric='angular', linkage_method='singular',
    #     n_clusters=1)


    ### CALENDARS
    # import pandas_market_calendars as mcal
    # # Create a calendar
    # nyse = mcal.get_calendar('NYSE')
    # schedule = nyse.schedule(start_date='2016-12-30', end_date='2017-01-10')
    # schedule  
    # # Show available calendars
    # print(mcal.get_calendar_names())


    # TRAIN TEST SPLIT
    X_train, X_test, y_train, y_test = train_test_split(
        X.drop(columns=['close_orig']), labeling_info['bin'],
        test_size=0.10, shuffle=False, stratify=None)


    ### SAMPLE WEIGHTS (DECAY FACTOR CAN BE ADDED!)
    if sample_weights_type == 'returns':
        sample_weigths = ml.sample_weights.get_weights_by_return(
            labeling_info.reindex(X_train.index),
            data.loc[X_train.index, 'close_orig'],
            num_threads=1)
    elif sample_weights_type == 'time_decay':
        sample_weigths = ml.sample_weights.get_weights_by_time_decay(
            labeling_info.reindex(X_train.index),
            data.loc[X_train.index, 'close_orig'],
            decay=0.5, num_threads=1)
    elif labeling_technique is 'trend_scanning':
        sample_weigths = labeling_info['t_value'].reindex(X_train.index).abs()


    ### CROS VALIDATION STEPS
    if cv_type == 'purged_kfold':
        cv = ml.cross_validation.PurgedKFold(
            n_splits=cv_number,
            samples_info_sets=labeling_info['t1'].reindex(X_train.index))


    # MODEL

    # parameters for GridSearch
    parameters = {'max_depth': [2, 3, 4, 5],
                'n_estimators': [500, 1000],
                'max_features': [5, 10, 15],
                'max_leaf_nodes': [4, 8, 16, 32]
                }

    # clf = joblib.load("rf_model.pkl")
    rf = RandomForestClassifier(criterion='entropy',
                                min_weight_fraction_leaf=0.05,
                                class_weight='balanced_subsample',
                                random_state=rand_state)
    clf = GridSearchCV(rf,
                    param_grid=parameters,
                    scoring='f1',
                    n_jobs=16,
                    cv=cv)
    clf.fit(X_train, y_train, sample_weight=sample_weigths)
    depth, n_features, n_estimators = clf.best_params_.values()

    # model scores
    clf_predictions = clf.predict(X_test)
    clf_f1_score = sklearn.metrics.f1_score(y_test, clf_predictions)
    print(f'f1_score: {clf_f1_score}')
    print(f'optimal_model_depth: {depth}')
    print(f'n_estimators: {n_estimators}')
    print(f'max_features {n_features}')


    # retrain the model if mean score is high enough (higher than 0.5)
    if clf_f1_score < 0.55:
        print('Bad performance')
    else:
        # refit best model and show results
        rf_best = RandomForestClassifier(criterion='entropy',
                                        max_features=n_features,
                                        min_weight_fraction_leaf=0.05,
                                        max_depth=depth,
                                        n_estimators=n_estimators,
                                        class_weight='balanced',
                                        n_jobs=16)
        rf_best.fit(X_train, y_train, sample_weight=sample_weigths)

        ### CLF METRICS
        tml.modeling.metrics_summary.clf_metrics(
            rf_best, X_train, X_test, y_train, y_test, avg='binary')  # HAVE TO FIX
        # tml.modeling.metrics_summary.plot_roc_curve(
        # clf, X_train, X_test, y_train, y_test, name='rf_')


        # ### FEATURE SELECTION
        fival = tml.modeling.feature_importance.feature_importance_values(
            rf_best, X_train, y_train)
        fivec = tml.modeling.feature_importance.feature_importnace_vec(
            fival, X_train)
        # tml.modeling.feature_importance.plot_feature_importance(fival, X_train, name='rf_')
        
        # ### REFIT THE MODEL WITH MOST IMPORTANT FEATURES
        X_train_important = X_train[
        fivec['col_name'].
        head(keep_important_features)]  #.drop(columns=['STOCHRSI_96000_fastk'])
        X_test_important = X_test[
        fivec['col_name'].
        head(keep_important_features)]  #.drop(columns=['STOCHRSI_96000_fastk'])
        clf_important = rf_best.fit(X_train_important, y_train)
        tml.modeling.metrics_summary.clf_metrics(
            clf_important, X_train_important,
            X_test_important, y_train, y_test, avg='binary', prefix='fi_')
        # tml.modeling.metrics_summary.plot_roc_curve(
        #     clf_important, X_train_important, X_test_important,
        #     y_train, y_test, suffix=' with importnat features', name='rf_fi_')


        ### BACKTESTING (RADI)

        # BUY-SELL BACKTESTING STRATEGY
        # true close 
        time_range = pd.date_range(X_test.index[0], X_test.index[-1], freq='1Min')
        close = data.close_orig.reindex(time_range).to_frame().dropna()
        # predictions on test set
        predictions = pd.Series(rf_best.predict(X_test_important), index=X_test_important.index)
        # plot cumulative returns
        hold_cash = tml.modeling.backtest.hold_cash_backtest(close, predictions)
        # fig = hold_cash[['close_orig', 'cum_return']].plot().get_figure()
        # fig.savefig(f'backtest_hold_cash.png')

        # # VECTORBT
        # positions = pd.concat([close, predictions.rename('position')], axis=1)
        # positions = tml.modeling.backtest.enter_positions(positions.values)
        # positions = pd.DataFrame(positions, index=close.index, columns=['close', 'position'])
        # entries = (positions[['position']] == 1).vbt.signals.first()  # buy at first 1
        # exits = (positions[['position']] == -1).vbt.signals.first()  # sell at first 0
        # portfolio = vbt.Portfolio.from_signals(close, entries, exits,
        #                                     slippage=vectorbt_slippage,
        #                                     fees=vectorbt_fees)
        # print(f'vectorbt_total_return: {portfolio.total_return}')

        # #TRIPLE-BARRIER BACKTEST
        # tbpred = labeling_info.loc[predictions.index]
        # tbpred['ret_adj'] = np.where(tbpred['bin']==predictions, np.abs(tbpred['ret']), -np.abs(tbpred['ret']))
        # total_return = (1 + tbpred['ret_adj']).cumprod().iloc[-1]
        # print(f'tb_return_nofees_noslippage: {total_return}')





        ### SAVE THE MODEL AND FEATURES
        # joblib.dump(clf, "rf_model_25_ts.pkl")
        # pd.Series(X_train_important.columns).to_csv('feature_names_25_ts.csv', sep=',')
        # serialized_model = tml.modeling.utils.serialize_random_forest(clf)
        # with open('rf_model_25_ts.json', 'w') as f:
        #     json.dump(serialized_model, f)


        ### BACKTEST STATISTICS 

        # def pyfolio_sheet(returns):
        #     daily_returns = returns.resample('D').mean().dropna()
        #     perf_func = pf.timeseries.perf_stats
        #     perf_stats_all = perf_func(returns=daily_returns, 
        #                                factor_returns=None)
        #     return perf_stats_all

        # strategy_pf = pyfolio_sheet(hold_cash['return'])
        # bencha_pf = pyfolio_sheet(data.close_orig.resample('D').last().
        #                             dropna().pct_change())
        # pf_sheet = pd.concat([bencha_pf.rename('banchmark'),
        #                       strategy_pf.rename('strategy')], axis=1)




        # import  mlfinlab.backtest_statistics as bs
        # def backtest_stat(returns):
        #     # RUNS
        #     pos_concentr, neg_concentr, hour_concentr = bs.all_bets_concentration(returns, frequency='min')
        #     drawdown, tuw = bs.drawdown_and_time_under_water(returns, dollars = False)
        #     drawdown_dollars, _ = bs.drawdown_and_time_under_water(returns, dollars = True)

        #     # EFFICIENCY
        #     days_observed = (price_series.index[-1] - price_series.index[0]) / np.timedelta64(1, 'D')
        #     cumulated_return = price_series[-1]/price_series[0]
        #     annual_return = (cumulated_return)**(365/days_observed) - 1
        #     print('Annualized average return from the portfolio is' , annual_return)

        #     # merge all statistics to dictionary
        #     backtest_statistics = {
        #         'Positive concetration': pos_concentr,
        #         'Negative concetration': neg_concentr,
        #         'Hour concetration': hour_concentr,
        #         'The 95th percentile Drawdown': drawdown.quantile(.95),
        #         'The 95th percentile Drawdown in dollars': drawdown_dollars.quantile(.95),
        #         'The 95th percentile of Time under water': tuw.quantile(.95),
        #         'Maximum Drawdown': drawdown.max(),
        #         'Maximum Drawdown in dolars': drawdown_dollars.max(),
        #         'Maximum Drawdown time': tuw.max()
        #     }
        #     # dictionary to dataframe    
        #     df = pd.DataFrame.from_dict(backtest_statistics, orient='index')

        #     return df


        # returns = hold_cash['return'].dropna()
        # price_series = hold_cash['adjusted_close'].dropna()
        # backtest_stat(returns)


        ############## TEST
        # model_features = pd.Series(X_train.columns)
        # min_d = pd.read_csv('min_d.csv', sep=';', names=['feature', 'value'])
        # min_d = min_d[1:]
        # min_d_close = min_d.loc[(min_d['feature'] == 'close') | (min_d['feature'] == 'open'), ['feature', 'value']]
        # min_d_close.set_index(min_d_close['feature'], inplace=True)
        # min_d_close = min_d_close['value']
        # min_d.set_index(min_d['feature'], inplace=True)

        # tripple barrier vector vs backtest
        # tb_fit.triple_barrier_info
        # tb_fit.triple_barrier_info.loc['2019-01-01 00:00:00':]
        # tb_fit.triple_barrier_info.loc['2016-07-07']
        # tb_fit.triple_barrier_info.loc['2016-07-07 00:00:00':].shape
        # 1000000 / 200
        # costs_per_transaction = (1000000 / 200) * 0.05
        # costs_per_transaction * tb_fit.triple_barrier_info.loc['2016-07-07 00:00:00':].shape[0]

        # # test multiplie orders
        # data.close_orig
        # test = ml.util.get_daily_vol(data.close_orig, lookback=50)
        # test[tb_fit.triple_barrier_info.index]

        # # extract close series
        # close_test = data.close_orig

        # # Compute volatility
        # daily_vol_test = ml.util.get_daily_vol(close_test, lookback=50)

        # # Apply Symmetric CUSUM Filter and get timestamps for events
        # cusum_events_test = ml.filters.cusum_filter(close_test,
        #     threshold=daily_vol_test.mean()*1)

        # # Compute vertical barrier
        # vertical_barriers_test = ml.labeling.add_vertical_barrier(
        #     t_events=cusum_events_test,
        #     close=close_test,
        #     num_days=2) 

        # # tripple barier events
        # triple_barrier_events_test = ml.labeling.get_events(
        #     close=close_test,
        #     t_events=cusum_events_test,
        #     pt_sl=[1, 1],
        #     target=daily_vol_test,
        #     min_ret=0.01,
        #     num_threads=1,
        #     vertical_barrier_times=vertical_barriers)

        # # labels
        # labels = ml.labeling.get_bins(triple_barrier_events, close)
        # labels = ml.labeling.drop_labels(labels)
        ############## TEST
